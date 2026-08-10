"""Sprints S10.1–S10.3 — deployment readiness.

Everything that can be checked without a server lives here. What genuinely
cannot — that the migrations run against a real Postgres, that the backup
scripts work against a real database — is listed in `docs/RUNBOOK.md §7` as
untested rather than quietly assumed.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.extensions import db
from app.models.enums import Role
from app.preflight import Level, run_all, worst_level
from tests.conftest import make_user

ROOT = Path(__file__).resolve().parent.parent


class TestPostgresCompatibility:
    """Every migration so far has only ever run on SQLite.

    A real Postgres run belongs in P10 on the server; this is what can be
    proven without one — that the schema at least *compiles* for the Postgres
    dialect and that the types map the way the design intends.
    """

    def test_every_table_compiles_for_postgres(self, app):
        dialect = postgresql.dialect()
        for table in db.metadata.sorted_tables:
            CreateTable(table).compile(dialect=dialect)
            for index in table.indexes:
                CreateIndex(index).compile(dialect=dialect)

    def test_event_timestamps_become_timestamptz(self, app):
        """`UtcDateTime` must not silently degrade to a naive column."""
        dialect = postgresql.dialect()
        aware = {
            ("users", "created_at"),
            ("attendance_records", "checked_in_at"),
            ("audit_log", "created_at"),
            ("enrollments", "paid_at"),
            ("whatsapp_messages", "sent_at"),
        }
        for table_name, column_name in aware:
            column = db.metadata.tables[table_name].columns[column_name]
            assert "WITH TIME ZONE" in column.type.compile(dialect=dialect), (
                f"{table_name}.{column_name} should be timestamptz"
            )

    def test_wall_clock_times_stay_without_timezone(self, app):
        """Session times are local wall clock; a tz-aware column would shift them."""
        dialect = postgresql.dialect()
        for table_name, column_name in (
            ("course_slots", "start_time"),
            ("sessions", "start_time"),
            ("sessions", "end_time"),
        ):
            compiled = db.metadata.tables[table_name].columns[column_name].type.compile(
                dialect=dialect
            )
            assert "WITHOUT TIME ZONE" in compiled, f"{table_name}.{column_name}"

    def test_enums_are_portable_varchars_not_native_types(self, app):
        """Native PG enums need an ALTER TYPE dance to add a value later.

        `native_enum=False` keeps them VARCHAR + CHECK, which is why the same
        migrations run unchanged on SQLite and Postgres.
        """
        dialect = postgresql.dialect()
        for table_name, column_name in (
            ("users", "role"),
            ("sessions", "status"),
            ("enrollments", "payment_status"),
            ("whatsapp_messages", "status"),
        ):
            compiled = db.metadata.tables[table_name].columns[column_name].type.compile(
                dialect=dialect
            )
            assert compiled.startswith("VARCHAR"), f"{table_name}.{column_name}={compiled}"

    def test_money_uses_numeric_not_float(self, app):
        """Float money accumulates rounding error; NUMERIC does not."""
        dialect = postgresql.dialect()
        for table_name, column_name in (
            ("courses", "price_egp"),
            ("enrollments", "amount_due"),
        ):
            compiled = db.metadata.tables[table_name].columns[column_name].type.compile(
                dialect=dialect
            )
            assert compiled.startswith("NUMERIC"), f"{table_name}.{column_name}"

    def test_migrations_carry_no_app_imports(self):
        """A migration must keep working after the app code around it changes.

        Importing `app.*` into a migration couples it to today's models, which
        is how a two-year-old migration stops running.
        """
        offenders = []
        for path in (ROOT / "migrations" / "versions").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "import app" in text or "from app" in text:
                offenders.append(path.name)
        assert not offenders, offenders


class TestPreflight:
    def test_it_fails_a_development_configuration(self, app):
        """Dev has a weak secret and insecure cookies; it must not pass."""
        checks = run_all(app)
        assert worst_level(checks) is Level.FAIL
        failed = {c.name for c in checks if c.level is Level.FAIL}
        assert "SECRET_KEY" in failed
        assert "Session cookies" in failed

    def test_it_flags_a_missing_admin(self, app, db, seeded_course_types, seeded_terms):
        checks = run_all(app)
        assert any(
            c.name == "Admin account" and c.level is Level.FAIL for c in checks
        )

    def test_it_passes_those_checks_once_setup_is_done(
        self, app, db, seeded_course_types, seeded_terms
    ):
        make_user(Role.ADMIN, phone="+201000000055", name="Real Owner")
        checks = {c.name: c for c in run_all(app)}
        for name in ("Admin account", "Course types", "Terms"):
            assert checks[name].level is Level.PASS, f"{name}: {checks[name].detail}"

        # The migration check cannot pass here: the test database is built with
        # `create_all()` and so has no alembic_version table. Warning rather
        # than failing on an unverifiable state is the intended behaviour.
        assert checks["Migrations"].level is Level.WARN

    def test_it_warns_about_demo_data(self, app, db, seeded_course_types, seeded_terms):
        """The seeded demo admin has a publicly known password."""
        make_user(Role.ADMIN, phone="+201000000001", name="Center Owner")
        checks = {c.name: c for c in run_all(app)}
        assert checks["Demo data"].level is Level.WARN

    def test_it_fails_when_the_whatsapp_link_is_unusable(self, app):
        """The send button is a redirect; a bad base silently opens nothing."""
        app.config["WHATSAPP_LINK_BASE"] = "wa.me"
        failed = {c.name for c in run_all(app) if c.level is Level.FAIL}
        assert "WhatsApp link" in failed

    def test_it_fails_when_translations_are_not_compiled(self, app, monkeypatch, tmp_path):
        """The failure this catches is invisible: Arabic silently renders English."""
        monkeypatch.setattr(app, "root_path", str(tmp_path))
        failed = {c.name for c in run_all(app) if c.level is Level.FAIL}
        assert "Translations (ar)" in failed


class TestDeploymentArtifacts:
    def test_the_scripts_are_executable(self):
        for name in (
            "deploy.sh",
            "rollback.sh",
            "backup.sh",
            "restore-drill.sh",
            "restore-drill-sqlite.sh",
        ):
            path = ROOT / "scripts" / name
            assert path.exists(), f"{name} missing"
            assert path.stat().st_mode & stat.S_IXUSR, f"{name} is not executable"

    def test_every_script_uses_strict_bash(self):
        """Without `set -e`, a failed backup step lets the deploy carry on."""
        for path in (ROOT / "scripts").glob("*.sh"):
            text = path.read_text(encoding="utf-8")
            assert "set -Eeuo pipefail" in text, f"{path.name} lacks strict mode"

    def test_the_deploy_backs_up_before_migrating(self):
        """Ordering is the whole point: a bad migration must be recoverable."""
        text = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
        assert text.index("backup.sh") < text.index("db upgrade")

    def test_the_deploy_gates_the_restart_on_preflight(self):
        text = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
        assert text.index("preflight") < text.index("systemctl restart")

    def test_the_deploy_compiles_translations(self):
        """`.mo` files are gitignored, so a deploy that skips this ships English."""
        text = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
        assert "pybabel" in text and "compile" in text

    def test_rollback_never_downgrades_the_database(self):
        """`db downgrade` drops columns; on live data that destroys records.

        Checks the executable lines only — the script *documents* why it does
        not downgrade, and that comment should not trip the check.
        """
        text = (ROOT / "scripts" / "rollback.sh").read_text(encoding="utf-8")
        executable = [
            line
            for line in text.splitlines()
            if not line.strip().startswith(("#", "echo"))
        ]
        assert not any("db downgrade" in line for line in executable), (
            "rollback.sh appears to run a migration downgrade"
        )
        # It should still *tell* the operator why it does not.
        assert "db downgrade" in text, "the warning text went missing"

    def test_nginx_forwards_the_real_client_ip(self):
        """Without this the login throttle throttles the proxy, i.e. everyone."""
        text = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
        assert "X-Forwarded-For" in text
        assert "X-Real-IP" in text

    def test_the_app_only_trusts_proxy_headers_in_production(self, app):
        """Believing X-Forwarded-For when exposed directly lets anyone spoof an IP."""
        from app.config import DevConfig, ProdConfig, TestConfig

        assert app.config["TRUST_PROXY_HEADERS"] is False
        assert DevConfig.TRUST_PROXY_HEADERS is False
        assert TestConfig.TRUST_PROXY_HEADERS is False
        assert ProdConfig.TRUST_PROXY_HEADERS is True

    def test_the_systemd_unit_restricts_writes(self):
        text = (ROOT / "deploy" / "mca.service").read_text(encoding="utf-8")
        assert "ProtectSystem=strict" in text
        assert "ReadWritePaths=/srv/mca/instance" in text
        assert "NoNewPrivileges=true" in text

    def test_no_deploy_unit_still_schedules_a_whatsapp_send(self):
        """There is no nightly job any more — sending needs a person. A leftover
        timer would fail every night into a log nobody reads."""
        leftovers = [p.name for p in (ROOT / "deploy").glob("*whatsapp*")]
        assert leftovers == []

    def test_gunicorn_allows_a_slow_request_to_finish(self):
        """No outbound API call any more, but a worker killed mid-request still
        leaves a message half-recorded."""
        text = (ROOT / "deploy" / "gunicorn.conf.py").read_text(encoding="utf-8")
        namespace: dict = {}
        exec(compile(text, "gunicorn.conf.py", "exec"), {"os": os}, namespace)
        assert namespace["timeout"] >= 30


class TestRunbook:
    def test_it_exists_and_covers_the_essentials(self):
        text = (ROOT / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")
        for topic in (
            "rollback",
            "restore",
            "preflight",
            "healthz",
            "seed-demo",
            "walkthrough",
        ):
            assert topic.lower() in text.lower(), f"runbook does not mention {topic}"

    def test_it_states_what_is_untested(self):
        """Known limitations belong in writing, not in someone's memory."""
        text = (ROOT / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")
        assert "Known limitations" in text
        assert "never been executed" in text  # the Postgres backup scripts

    @pytest.mark.parametrize("secret_key", ["SECRET_KEY"])
    def test_the_env_template_ships_no_values(self, secret_key):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith(f"{secret_key}="):
                assert line == f"{secret_key}=", f"{secret_key} has a committed value"

    def test_the_env_template_asks_for_no_whatsapp_credential(self):
        """Removing the API removed the only third-party secret in the app.

        A leftover key in the template invites someone to fill it in and wonder
        for an afternoon why nothing sends.
        """
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for banned in ("ACCESS_TOKEN", "APP_SECRET", "VERIFY_TOKEN", "PHONE_NUMBER_ID"):
            assert banned not in text, f".env.example still asks for {banned}"
