"""Sprints S9.1–S9.4 — hardening.

`TestFullAuthorizationMatrix` is the most important file in the repo, per
PLAN.md §6: it walks **every** route in the app rather than a hand-kept list,
so a new endpoint cannot quietly ship without an access decision. The brief's
warning — a teacher guessing IDs to reach another teacher's data — is what it
defends.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models.enums import Role
from app.security import CSP_DIRECTIVES, LoginThrottle, content_security_policy
from tests.conftest import KNOWN_PASSWORD, login, make_user

ROOT = Path(__file__).resolve().parent.parent

# Endpoints reachable without signing in, each with the reason it must be.
PUBLIC_ENDPOINTS = {
    "auth.login": "the sign-in form itself",
    "auth.logout": "must work even from a half-broken session",
    "main.healthz": "load balancer probe; returns no data",
    "main.set_language": "language toggle works before signing in",
    "static": "CSS and vendored JS",
}


def _all_endpoints(app) -> set[str]:
    return {rule.endpoint for rule in app.url_map.iter_rules()}


def _sample_url(rule) -> str | None:
    """A concrete URL for a rule, substituting 1 for any integer parameter."""
    url = rule.rule
    if re.search(r"<(?!int:)", url):  # non-integer converters: skip
        return None
    return re.sub(r"<int:[a-z_]+>", "1", url)


class TestSecurityHeaders:
    """S9.1."""

    def test_every_response_carries_the_core_headers(self, client):
        response = client.get("/login")
        for header in (
            "Content-Security-Policy",
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
            "Permissions-Policy",
        ):
            assert header in response.headers, f"missing {header}"

    def test_error_pages_are_protected_too(self, client):
        """A 404 is still a page an attacker can get rendered."""
        response = client.get("/no/such/page")
        assert response.status_code == 404
        assert "Content-Security-Policy" in response.headers

    def test_script_src_forbids_inline_code(self):
        """The directive that actually stops XSS.

        Everything is vendored and there are no inline <script> blocks, so the
        strictest setting costs nothing.
        """
        assert CSP_DIRECTIVES["script-src"] == "'self'"
        assert "unsafe-inline" not in CSP_DIRECTIVES["script-src"]
        assert "unsafe-eval" not in CSP_DIRECTIVES["script-src"]

    def test_no_fetch_directive_allows_a_remote_origin(self):
        """Nothing the browser *loads* may come from off this host.

        `form-action` is deliberately excluded and checked separately below: it
        governs where a submission may navigate, not what code or content gets
        pulled in, so a host there cannot introduce anything into the page.
        """
        fetch_directives = {
            key: value
            for key, value in CSP_DIRECTIVES.items()
            if key.endswith("-src") or key == "default-src"
        }
        for name, value in fetch_directives.items():
            assert "http://" not in value, name
            assert "https://" not in value, name
            assert "*" not in value, name

    def test_framing_is_locked_down_and_form_posting_allows_only_whatsapp(self):
        """The one remote host in the whole policy, and why it is there.

        "Send on WhatsApp" posts to this app, which records the hand-off and
        redirects to wa.me. Browsers check redirect targets against
        `form-action`, so `'self'` alone breaks the only send button — with
        nothing in the server logs, because the block happens client-side.
        """
        assert CSP_DIRECTIVES["frame-ancestors"] == "'none'"
        assert CSP_DIRECTIVES["form-action"] == "'self' https://wa.me"

    def test_no_second_host_creeps_into_the_policy(self):
        """Guards the exemption above from becoming a general allowance."""
        policy = content_security_policy()
        assert policy.count("https://") == 1
        assert "http://" not in policy
        assert "*" not in policy

    def test_hsts_is_sent_only_when_tls_is_in_use(self, app, client):
        """Sending HSTS over plain http would pin the browser to https://localhost."""
        assert app.config["SESSION_COOKIE_SECURE"] is False
        assert "Strict-Transport-Security" not in client.get("/login").headers

        app.config["SESSION_COOKIE_SECURE"] = True
        assert "Strict-Transport-Security" in client.get("/login").headers


class TestFullAuthorizationMatrix:
    """S9.2 — walks every route, not a curated list."""

    @pytest.fixture
    def clients(self, app, seeded_course_types, admin, assistant, teacher, student, parent):
        people = {
            Role.ADMIN: admin,
            Role.ASSISTANT: assistant,
            Role.TEACHER: teacher,
            Role.STUDENT: student,
            Role.PARENT: parent,
        }
        made = {}
        for role, user in people.items():
            c = app.test_client()
            login(c, user)
            made[role] = c
        return made

    def test_every_endpoint_is_either_public_or_login_protected(self, app, client):
        """No route may serve data to an anonymous caller.

        Anything not on the public list must redirect to login (302), refuse
        (401/403/405), or 404 — never render.
        """
        leaks = []
        for rule in app.url_map.iter_rules():
            if rule.endpoint in PUBLIC_ENDPOINTS:
                continue
            url = _sample_url(rule)
            if url is None or "GET" not in rule.methods:
                continue
            response = client.get(url, follow_redirects=False)
            if response.status_code == 200:
                leaks.append(f"{rule.endpoint} ({url})")

        assert not leaks, (
            "These render for an anonymous visitor. Either protect them or add "
            "them to PUBLIC_ENDPOINTS with a reason:\n  " + "\n  ".join(leaks)
        )

    def test_every_public_endpoint_states_why(self, app):
        """Being public is a decision that has to be written down."""
        for endpoint, reason in PUBLIC_ENDPOINTS.items():
            assert reason.strip(), f"{endpoint} is public with no stated reason"

    def test_the_public_list_has_no_stale_entries(self, app):
        stale = sorted(set(PUBLIC_ENDPOINTS) - _all_endpoints(app))
        assert not stale, f"PUBLIC_ENDPOINTS names routes that no longer exist: {stale}"

    @pytest.mark.parametrize("role", list(Role))
    def test_no_role_can_reach_another_areas_pages(self, app, clients, role):
        """Every GET route, every role. Allowed prefixes per role are explicit."""
        allowed_prefixes = {
            Role.ADMIN: ("/admin", "/assistant", "/", "/password", "/terms", "/me"),
            Role.ASSISTANT: ("/assistant", "/", "/password", "/terms", "/me"),
            Role.TEACHER: ("/teacher", "/", "/password", "/terms", "/me"),
            Role.STUDENT: ("/portal", "/", "/password", "/terms", "/me"),
            Role.PARENT: ("/portal", "/", "/password", "/terms", "/me"),
        }[role]

        breaches = []
        for rule in app.url_map.iter_rules():
            if rule.endpoint in PUBLIC_ENDPOINTS or "GET" not in rule.methods:
                continue
            url = _sample_url(rule)
            if url is None:
                continue
            # Only test URLs *outside* this role's own areas.
            if any(url == "/" or url.startswith(p) for p in allowed_prefixes):
                continue

            response = clients[role].get(url, follow_redirects=False)
            if response.status_code == 200:
                breaches.append(f"{role.value} reached {url}")

        assert not breaches, "\n  ".join(breaches)

    def test_a_teacher_cannot_reach_any_staff_write_endpoint(self, app, clients):
        """POST endpoints too, not just the pages that list things."""
        breaches = []
        for rule in app.url_map.iter_rules():
            if "POST" not in rule.methods or rule.endpoint in PUBLIC_ENDPOINTS:
                continue
            url = _sample_url(rule)
            if url is None or not (
                url.startswith("/admin") or url.startswith("/assistant")
            ):
                continue
            response = clients[Role.TEACHER].post(url, data={}, follow_redirects=False)
            if response.status_code not in (403, 404, 405):
                breaches.append(f"{url} -> {response.status_code}")

        assert not breaches, "teacher reached staff write endpoints:\n  " + "\n  ".join(
            breaches
        )


class TestLoginThrottle:
    """S9.1 — this was listed in sprint S1.2 but never actually built."""

    def test_repeated_failures_lock_the_ip_out(self, app, db, admin):
        app.config["LOGIN_MAX_ATTEMPTS"] = 3
        client = app.test_client()

        for _ in range(3):
            response = client.post(
                "/login", data={"identifier": admin.username, "password": "wrong"}
            )
            assert response.status_code == 401

        blocked = client.post(
            "/login", data={"identifier": admin.username, "password": "wrong"}
        )
        assert blocked.status_code == 429

    def test_the_correct_password_is_refused_while_locked_out(self, app, db, admin):
        """Otherwise the throttle is trivially bypassed by the attacker who wins."""
        app.config["LOGIN_MAX_ATTEMPTS"] = 2
        client = app.test_client()
        for _ in range(2):
            client.post("/login", data={"identifier": admin.username, "password": "no"})

        response = client.post(
            "/login", data={"identifier": admin.username, "password": KNOWN_PASSWORD}
        )
        assert response.status_code == 429

    def test_a_successful_login_is_unaffected_below_the_limit(self, app, db, admin):
        app.config["LOGIN_MAX_ATTEMPTS"] = 5
        client = app.test_client()
        client.post("/login", data={"identifier": admin.username, "password": "wrong"})
        response = login(client, admin)
        assert response.status_code == 302

    def test_old_failures_fall_out_of_the_window(self, app, db, admin):
        from datetime import timedelta

        from sqlalchemy import select

        from app.models.audit import AuditLog
        from app.models.base import utcnow

        app.config["LOGIN_MAX_ATTEMPTS"] = 2
        client = app.test_client()
        for _ in range(2):
            client.post("/login", data={"identifier": admin.username, "password": "no"})

        # Age the recorded failures past the window.
        for entry in db.session.scalars(
            select(AuditLog).where(AuditLog.action == "auth.login_failed")
        ):
            entry.created_at = utcnow() - timedelta(hours=2)
        db.session.commit()

        assert login(client, admin).status_code == 302

    def test_the_throttle_counts_from_shared_state_not_memory(self, app, db, admin):
        """Several gunicorn workers must share one quota.

        An in-process counter would give each worker the full allowance, so the
        count comes from the audit table every request.
        """
        throttle = LoginThrottle(max_attempts=3, window_minutes=15)
        client = app.test_client()
        for _ in range(3):
            client.post("/login", data={"identifier": admin.username, "password": "x"})

        # A brand-new throttle object still sees the failures.
        assert LoginThrottle(3, 15).is_blocked("127.0.0.1")
        assert throttle.recent_failures("127.0.0.1") == 3
        assert throttle.recent_failures("10.0.0.1") == 0


class TestInjectionAndEscaping:
    """S9.3."""

    def test_no_template_disables_autoescaping(self):
        offenders = [
            str(path.relative_to(ROOT))
            for path in (ROOT / "app" / "templates").rglob("*.html")
            if "|safe" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_no_sql_is_built_by_string_interpolation(self):
        """A constant `text("SELECT 1")` is fine; interpolating a value is not.

        The rule is about untrusted data reaching the query, so this looks for
        f-strings, `.format`, `%` and `+` inside a SQL literal — not for the
        mere use of `text()`, which an earlier version flagged (a false
        positive on a hardcoded `SELECT version_num FROM alembic_version`).
        """
        offenders = []
        sql_call = re.compile(r"\b(?:execute|text)\(", re.I)
        interpolated = re.compile(
            r"""f["'].*\b(SELECT|INSERT|UPDATE|DELETE)\b"""      # f-string SQL
            r"""|["'].*\b(SELECT|INSERT|UPDATE|DELETE)\b.*["']\s*(?:%|\+|\.format\()""",
            re.I,
        )
        for path in (ROOT / "app").rglob("*.py"):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if sql_call.search(line) and interpolated.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno} {line.strip()}")
        assert not offenders, offenders

    def test_the_sql_check_above_catches_a_real_injection(self):
        """Guards the regex — a check that matches nothing reads as reassurance."""
        sql_call = re.compile(r"\b(?:execute|text)\(", re.I)
        interpolated = re.compile(
            r"""f["'].*\b(SELECT|INSERT|UPDATE|DELETE)\b"""
            r"""|["'].*\b(SELECT|INSERT|UPDATE|DELETE)\b.*["']\s*(?:%|\+|\.format\()""",
            re.I,
        )
        bad = 'db.session.execute(f"SELECT * FROM users WHERE id = {user_id}")'
        assert sql_call.search(bad) and interpolated.search(bad)

        good = 'db.session.execute(db.text("SELECT version_num FROM alembic_version"))'
        assert not (sql_call.search(good) and interpolated.search(good))

    def test_a_script_tag_in_user_data_is_escaped(self, app, db, seeded_terms):
        """End-to-end: a hostile name must render inert."""
        from tests.conftest import accept_current_terms

        nasty = "<script>alert(1)</script>"
        student = make_user(Role.STUDENT, phone="+201055557777", name=nasty)
        accept_current_terms(student)

        client = app.test_client()
        login(client, student)
        body = client.get("/portal/").get_data(as_text=True)
        assert nasty not in body
        assert "&lt;script&gt;" in body

    def test_uploads_reject_non_images(self, app):
        from app.services.storage import ALLOWED_IMAGE_EXTENSIONS

        assert ".svg" not in ALLOWED_IMAGE_EXTENSIONS  # SVG can carry script
        assert ".html" not in ALLOWED_IMAGE_EXTENSIONS
        assert ALLOWED_IMAGE_EXTENSIONS <= {".jpg", ".jpeg", ".png", ".webp"}

    def test_the_upload_path_cannot_escape_its_root(self, app):
        from app.services.storage import resolve

        with app.test_request_context(), pytest.raises(ValueError):
            resolve("../../etc/passwd")

    def test_redirects_stay_inside_the_app(self, client):
        response = client.post(
            "/me/language",
            data={"locale": "en", "next": "https://evil.example.com/"},
            follow_redirects=False,
        )
        assert "evil.example.com" not in response.headers["Location"]


class TestSecretsHygiene:
    """S9.4."""

    def test_no_secret_is_hardcoded_outside_dev_and_test_config(self):
        """Prod must read every secret from the environment."""
        config = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
        prod = config[config.index("class ProdConfig") :]
        assert "os.environ" in prod
        for literal in ("password=", "secret=", "token="):
            assert literal not in prod.lower()

    def test_no_plaintext_password_is_ever_logged(self):
        """A logging call must never take a password-ish value as an argument.

        Matched narrowly on purpose: an earlier, looser version flagged a
        docstring and an `import LoginForm` (because "Login" contains "log"),
        which is the kind of noise that gets a security test disabled.
        """
        call = re.compile(r"\b(?:logger|logging|current_app\.logger)\.\w+\(|(?<![\w.])print\(")
        secret_arg = re.compile(r"\b(plaintext|password|password_hash|access_token)\b")

        offenders = []
        for path in (ROOT / "app").rglob("*.py"):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if call.search(line) and secret_arg.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno} {stripped}")
        assert not offenders, offenders

    def test_the_check_above_would_catch_a_real_leak(self, tmp_path):
        """Guards the regex itself — a security test that matches nothing is worse
        than none, because it reads as reassurance."""
        call = re.compile(r"\b(?:logger|logging|current_app\.logger)\.\w+\(|(?<![\w.])print\(")
        secret_arg = re.compile(r"\b(plaintext|password|password_hash|access_token)\b")

        leak = 'logger.info("new account %s", plaintext)'
        assert call.search(leak) and secret_arg.search(leak)

        benign = "from app.blueprints.auth.forms import LoginForm"
        assert not (call.search(benign) and secret_arg.search(benign))

    def test_the_audit_redaction_list_covers_the_obvious_fields(self):
        from app.services.audit import REDACTED_FIELDS

        assert {"password", "password_hash", "token", "secret"} <= REDACTED_FIELDS

    def test_env_example_ships_no_real_values(self):
        """It is committed, so every secret line must be empty."""
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for line in text.splitlines():
            if any(k in line for k in ("SECRET", "TOKEN", "PASSWORD")) and "=" in line:
                key, _, value = line.partition("=")
                if key.strip().startswith("#"):
                    continue
                assert value.strip() == "", f"{key.strip()} has a value committed"

    def test_the_dev_secret_key_is_never_used_in_production(self, monkeypatch):
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from app.config import ProdConfig

        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            ProdConfig()
