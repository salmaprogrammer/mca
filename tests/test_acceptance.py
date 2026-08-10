"""Sprints S11.1–S11.3 — the final acceptance audit.

`docs/ACCEPTANCE.md` maps every requirement in the brief to the code and the
test that satisfies it. A traceability table nobody checks drifts within a
month, so this file verifies that every test it names actually exists, and that
the requirements it admits are *unmet* are still the only unmet ones.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.models.enums import Role
from tests.conftest import login

ROOT = Path(__file__).resolve().parent.parent
ACCEPTANCE = ROOT / "docs" / "ACCEPTANCE.md"
TESTS_DIR = ROOT / "tests"

# `test_file.py::TestClass::test_name`, with the class and test optional.
REFERENCE = re.compile(r"\b(test_[a-z0-9_]+\.py)(?:::([A-Za-z0-9_]+))?(?:::([a-z0-9_]+))?")


def _defined_names(path: Path) -> set[str]:
    """Every top-level class/function and `Class::method` pair in a test file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names.add(node.name)
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    names.add(f"{node.name}::{child.name}")
                    names.add(child.name)
        elif isinstance(node, ast.FunctionDef):
            names.add(node.name)
    return names


@pytest.fixture(scope="module")
def acceptance_text() -> str:
    assert ACCEPTANCE.exists(), "docs/ACCEPTANCE.md is missing"
    return ACCEPTANCE.read_text(encoding="utf-8")


class TestTraceability:
    """S11.1."""

    def test_every_referenced_test_exists(self, acceptance_text):
        """A requirement pointing at a deleted test is worse than no pointer.

        It reads as evidence while proving nothing.
        """
        missing = []
        for filename, class_name, test_name in REFERENCE.findall(acceptance_text):
            path = TESTS_DIR / filename
            if not path.exists():
                missing.append(f"{filename} (file not found)")
                continue

            names = _defined_names(path)
            if class_name and test_name:
                if f"{class_name}::{test_name}" not in names:
                    missing.append(f"{filename}::{class_name}::{test_name}")
            elif class_name and class_name not in names:
                missing.append(f"{filename}::{class_name}")

        assert not missing, (
            "docs/ACCEPTANCE.md points at tests that do not exist:\n  "
            + "\n  ".join(missing)
        )

    def test_it_references_a_realistic_number_of_tests(self, acceptance_text):
        """Guards the regex: a table matching nothing would pass silently."""
        assert len(REFERENCE.findall(acceptance_text)) > 40

    def test_every_requirement_row_has_a_verdict(self, acceptance_text):
        """Each numbered row must cite a test or say why it does not."""
        unresolved = []
        for line in acceptance_text.splitlines():
            if not re.match(r"\|\s*\d+\.\d+\s*\|", line):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            verdict = cells[-1]
            if verdict in ("", "—"):
                # An empty verdict is only acceptable if the row says so.
                if "Not implemented" not in line and "Deferred" not in line:
                    unresolved.append(line.strip())
        assert not unresolved, (
            "These requirement rows cite no test and give no reason:\n  "
            + "\n  ".join(unresolved)
        )

    def test_the_unmet_requirements_are_the_documented_ones(self, acceptance_text):
        """If something new becomes unmet, this forces it to be written down."""
        unmet = {
            re.match(r"\|\s*(\d+\.\d+)\s*\|", line).group(1)
            for line in acceptance_text.splitlines()
            if re.match(r"\|\s*\d+\.\d+\s*\|", line)
            and ("Not implemented" in line or "Deferred" in line)
        }
        # 1.14 teacher calendar editing, 4.7 teacher self check-in,
        # 6.4 the WhatsApp API, removed in favour of sending by hand.
        assert unmet == {"1.14", "4.7", "6.4"}, f"unmet set changed: {sorted(unmet)}"

    def test_the_deviation_is_explained_not_just_listed(self, acceptance_text):
        """A requirement deliberately not met must justify itself."""
        section = acceptance_text[acceptance_text.index("### 9.1") :]
        assert "Assistant" in section  # the terms clause it defers to
        assert "24" in section  # the 24-hour notice rule
        assert "small change" in section  # and what it would take to change

    def test_removing_the_whatsapp_api_states_what_was_lost(self, acceptance_text):
        """Dropping the API traded capability for speed. Anyone accepting this
        build has to be told what stopped working, not only what got easier."""
        section = acceptance_text[acceptance_text.index("### 9.6") :]
        assert "No delivery confirmation" in section
        assert "No bulk send" in section
        assert "6.3" in section  # messages no longer come from the centre's number


def test_proxy_fix_is_absent_by_default(app):
    """Believing X-Forwarded-For when exposed directly lets anyone spoof an IP,
    so only production installs it."""
    from werkzeug.middleware.proxy_fix import ProxyFix

    assert not isinstance(app.wsgi_app, ProxyFix)


class TestProductionScoping:
    """S11.2 — re-verify the wall under production-shaped configuration.

    Deploy-time config changes are where authorization regressions hide: a
    `ProxyFix` that rewrites the remote address, a cookie flag that changes
    session behaviour. The full matrix lives in `test_security_hardening.py`;
    this re-runs the sharpest cases with production settings applied.
    """

    @pytest.fixture
    def prod_like(self, monkeypatch):
        """An app built *with* production settings, not patched afterwards.

        `TRUST_PROXY_HEADERS` is read inside `create_app` to decide whether to
        install `ProxyFix`, so setting it on an already-built app does nothing —
        a distinction that cost a confusing test failure to find, and one worth
        knowing before someone tries to toggle it at runtime in production.
        """
        from app import create_app
        from app.config import TestConfig
        from app.extensions import db as _db
        from tests.conftest import _clear_per_request_caches

        monkeypatch.setattr(TestConfig, "TRUST_PROXY_HEADERS", True, raising=False)
        monkeypatch.setattr(TestConfig, "SESSION_COOKIE_SECURE", True, raising=False)
        monkeypatch.setattr(TestConfig, "PREFERRED_URL_SCHEME", "https", raising=False)

        application = create_app("testing")
        application.before_request_funcs.setdefault(None, []).insert(
            0, _clear_per_request_caches
        )
        with application.app_context():
            _db.create_all()
            yield application
            _db.session.remove()
            _db.drop_all()

    @pytest.fixture
    def prod_admin(self, prod_like):
        from app.models.enums import Role
        from tests.conftest import make_user

        return make_user(Role.ADMIN, phone="+201000007777", name="Prod Owner")

    @pytest.fixture
    def prod_teacher(self, prod_like):
        from app.models.enums import Role, TermsAudience
        from app.seeds.terms_v1 import STUDENT_PARENT_TERMS_AR, TEACHER_TERMS_AR
        from app.services import terms as terms_service
        from tests.conftest import accept_current_terms, make_user

        terms_service.publish_version(
            None, TermsAudience.TEACHER, body_ar=TEACHER_TERMS_AR
        )
        terms_service.publish_version(
            None, TermsAudience.STUDENT_PARENT, body_ar=STUDENT_PARENT_TERMS_AR
        )
        user = make_user(Role.TEACHER, phone="+201011117777")
        accept_current_terms(user)
        return user

    def test_proxy_fix_is_installed_when_configured(self, prod_like):
        """The setting is creation-time, which is exactly why it is tested."""
        from werkzeug.middleware.proxy_fix import ProxyFix

        assert isinstance(prod_like.wsgi_app, ProxyFix)

    def test_a_teacher_still_cannot_reach_the_admin_area(self, prod_like, prod_teacher):
        client = prod_like.test_client()
        login(client, prod_teacher)
        assert client.get("/admin/", base_url="https://localhost").status_code == 403

    def test_a_parent_still_cannot_reach_another_familys_child(
        self, prod_like, prod_teacher
    ):
        """`prod_teacher` seeds the terms; the families are built here."""
        from tests.conftest import accept_current_terms, link_parent, make_user

        mine = make_user(Role.STUDENT, phone="+201055558881")
        my_parent = make_user(Role.PARENT, phone="+201099998881")
        link_parent(my_parent, mine)
        accept_current_terms(my_parent)

        theirs = make_user(Role.STUDENT, phone="+201055558882")
        their_parent = make_user(Role.PARENT, phone="+201099998882")
        link_parent(their_parent, theirs)

        client = prod_like.test_client()
        login(client, my_parent)
        response = client.get(
            f"/portal/students/{theirs.id}", base_url="https://localhost"
        )
        assert response.status_code == 404

    def test_anonymous_access_is_still_refused(self, prod_like):
        client = prod_like.test_client()
        for url in ("/admin/", "/assistant/", "/teacher/", "/portal/"):
            response = client.get(
                url, base_url="https://localhost", follow_redirects=False
            )
            assert response.status_code == 302
            assert "/login" in response.headers["Location"]

    def test_security_headers_include_hsts_under_tls(self, prod_like):
        client = prod_like.test_client()
        response = client.get("/login", base_url="https://localhost")
        assert "Strict-Transport-Security" in response.headers
        assert "Content-Security-Policy" in response.headers

    def test_the_throttle_buckets_by_the_forwarded_client_ip(
        self, prod_like, prod_admin
    ):
        """With ProxyFix on, the throttle keys on X-Forwarded-For.

        That is correct **only because nginx sets that header rather than
        appending to a client-supplied one** — see deploy/nginx.conf. If the
        proxy ever passes a client value through, an attacker rotates the
        header and the throttle stops working.
        """
        prod_like.config["LOGIN_MAX_ATTEMPTS"] = 2
        admin = prod_admin
        client = prod_like.test_client()

        for _ in range(2):
            client.post(
                "/login",
                data={"identifier": admin.username, "password": "wrong"},
                base_url="https://localhost",
                headers={"X-Forwarded-For": "9.9.9.9"},
            )

        blocked = client.post(
            "/login",
            data={"identifier": admin.username, "password": "wrong"},
            base_url="https://localhost",
            headers={"X-Forwarded-For": "9.9.9.9"},
        )
        assert blocked.status_code == 429

        # A different forwarded IP is a different bucket — which is why nginx
        # must *set* this header rather than append to a client-supplied one.
        fresh = client.post(
            "/login",
            data={"identifier": admin.username, "password": "wrong"},
            base_url="https://localhost",
            headers={"X-Forwarded-For": "8.8.8.8"},
        )
        assert fresh.status_code == 401


class TestDataIntegrity:
    """S11.3."""

    def test_a_clean_database_reports_nothing(self, app, db, seeded_terms):
        from app.integrity import run_all

        assert run_all() == []

    def test_it_detects_a_payment_status_contradiction(self, app, db, seeded_terms,
                                                       seeded_course_types, admin):
        from app.integrity import run_all
        from app.models.base import utcnow
        from app.services import enrollments as enrollment_service
        from tests.conftest import make_course, make_user

        teacher = make_user(Role.TEACHER, phone="+201011117111")
        student = make_user(Role.STUDENT, phone="+201055557111")
        course = make_course(admin, teacher=teacher, course_type_code="sat_intermediate",
                             slots=[{"weekday": 6, "start_time": "10:00"}])
        enrollment = enrollment_service.enroll(admin, course, student)

        # Unpaid but carrying a payment date — a report would call this paid.
        enrollment.paid_at = utcnow()
        db.session.commit()

        findings = {f.check for f in run_all()}
        assert "payment status and paid_at disagree" in findings

    def test_it_detects_an_absent_student_with_a_check_in_time(
        self, app, db, seeded_terms, seeded_course_types, admin
    ):
        from datetime import date

        from app.integrity import run_all
        from app.models.base import utcnow
        from app.models.enums import AttendanceStatus
        from app.services import attendance as attendance_service
        from app.services import enrollments as enrollment_service
        from app.services import sessions as session_service
        from tests.conftest import make_course, make_user

        teacher = make_user(Role.TEACHER, phone="+201011117222")
        student = make_user(Role.STUDENT, phone="+201055557222")
        course = make_course(
            admin, teacher=teacher, course_type_code="sat_intermediate",
            slots=[{"weekday": 6, "start_time": "10:00"}], start_date=date(2026, 9, 6),
        )
        enrollment_service.enroll(admin, course, student)
        session_service.generate_sessions(admin, course)

        record = attendance_service.mark_student(
            admin, course.sessions[0], student, AttendanceStatus.ABSENT
        )
        record.checked_in_at = utcnow()  # nonsense a constraint cannot forbid
        db.session.commit()

        findings = {f.check for f in run_all()}
        assert "absent students with a check-in time" in findings

    def test_it_reports_without_repairing(self, app, db, seeded_terms,
                                          seeded_course_types, admin):
        """The right fix for a damaged record is a judgement call about a real
        family's data, so the tool must never make it."""
        from app.integrity import run_all
        from app.models.base import utcnow
        from app.models.course import Enrollment
        from app.services import enrollments as enrollment_service
        from tests.conftest import make_course, make_user

        teacher = make_user(Role.TEACHER, phone="+201011117333")
        student = make_user(Role.STUDENT, phone="+201055557333")
        course = make_course(admin, teacher=teacher, course_type_code="sat_intermediate",
                             slots=[{"weekday": 6, "start_time": "10:00"}])
        enrollment = enrollment_service.enroll(admin, course, student)
        enrollment.paid_at = utcnow()
        db.session.commit()

        run_all()
        db.session.expire_all()
        assert db.session.get(Enrollment, enrollment.id).paid_at is not None


class TestHandover:
    """S11.4 — the documents someone inherits this with."""

    @pytest.mark.parametrize(
        "doc", ["README.md", "PLAN.md", "docs/RUNBOOK.md", "docs/ACCEPTANCE.md",
                "docs/HANDOVER.md"]
    )
    def test_the_document_exists(self, doc):
        assert (ROOT / doc).exists(), f"{doc} is missing"

    def test_the_handover_lists_the_unanswered_questions(self):
        text = (ROOT / "docs" / "HANDOVER.md").read_text(encoding="utf-8")
        # The open questions still outstanding at handover.
        for number in ("6", "8", "9", "10", "11", "12", "13"):
            assert f"Q{number}" in text, f"open question {number} not carried over"

    def test_the_handover_states_what_was_never_run(self):
        text = (ROOT / "docs" / "HANDOVER.md").read_text(encoding="utf-8")
        assert "Postgres" in text
        assert "native" in text.lower()  # the Arabic review
        assert "Meta" in text
