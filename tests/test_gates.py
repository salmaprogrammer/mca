"""Sprints S1.3 and S1.5 — the two onboarding gates.

The point of every test here is that the wall is server-side. Typing a URL
directly must not walk around it.
"""

from __future__ import annotations

import pytest

from app.models.enums import Role, TermsAudience
from app.services import terms as terms_service
from tests.conftest import KNOWN_PASSWORD, accept_current_terms, login, make_user

PROTECTED_URLS = ["/", "/admin/", "/assistant/", "/teacher/", "/portal/"]


class TestPasswordGate:
    @pytest.fixture
    def fresh_user(self, db, seeded_terms):
        return make_user(Role.TEACHER, phone="+201012340000", must_change_password=True)

    @pytest.mark.parametrize("url", PROTECTED_URLS)
    def test_every_url_redirects_to_the_change_form(self, app, fresh_user, url):
        client = app.test_client()
        login(client, fresh_user)
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 302
        assert "/password/change" in response.headers["Location"]

    def test_the_change_form_itself_is_reachable(self, app, fresh_user):
        client = app.test_client()
        login(client, fresh_user)
        assert client.get("/password/change").status_code == 200

    def test_logout_is_reachable_while_gated(self, app, fresh_user):
        client = app.test_client()
        login(client, fresh_user)
        assert client.post("/logout", follow_redirects=False).status_code == 302

    def test_changing_the_password_lifts_the_gate(self, app, db, fresh_user):
        client = app.test_client()
        login(client, fresh_user)
        client.post(
            "/password/change",
            data={
                "current_password": KNOWN_PASSWORD,
                "new_password": "BrandNewPass1",
                "confirm_password": "BrandNewPass1",
            },
        )
        db.session.refresh(fresh_user)
        assert fresh_user.must_change_password is False

        # Now only the terms gate remains.
        response = client.get("/", follow_redirects=False)
        assert "/terms" in response.headers["Location"]

    def test_wrong_current_password_is_refused(self, app, db, fresh_user):
        client = app.test_client()
        login(client, fresh_user)
        response = client.post(
            "/password/change",
            data={
                "current_password": "not-it",
                "new_password": "BrandNewPass1",
                "confirm_password": "BrandNewPass1",
            },
        )
        assert response.status_code == 400
        db.session.refresh(fresh_user)
        assert fresh_user.must_change_password is True


class TestTermsGate:
    @pytest.fixture
    def unaccepted_teacher(self, db, seeded_terms):
        return make_user(Role.TEACHER, phone="+201012341111")

    @pytest.mark.parametrize("url", PROTECTED_URLS)
    def test_every_url_redirects_to_terms(self, app, unaccepted_teacher, url):
        client = app.test_client()
        login(client, unaccepted_teacher)
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 302
        assert "/terms" in response.headers["Location"]

    def test_the_screen_shows_the_teacher_text(self, app, unaccepted_teacher):
        client = app.test_client()
        login(client, unaccepted_teacher)
        body = client.get("/terms").get_data(as_text=True)
        assert "٦٠٪" in body  # the revenue-split clause from the teacher terms

    def test_students_get_the_student_text_not_the_teacher_one(self, app, db, seeded_terms):
        student = make_user(Role.STUDENT, phone="+201012342222")
        client = app.test_client()
        login(client, student)
        body = client.get("/terms").get_data(as_text=True)
        assert "٦٠٪" not in body
        assert "سياسة الحضور والغياب" in body

    def test_cannot_pass_without_ticking_the_box(self, app, db, unaccepted_teacher):
        client = app.test_client()
        login(client, unaccepted_teacher)
        client.post("/terms", data={})
        assert terms_service.needs_acceptance(unaccepted_teacher) is True

    def test_accepting_records_who_what_and_when(self, app, db, unaccepted_teacher, seeded_terms):
        client = app.test_client()
        login(client, unaccepted_teacher)
        client.post("/terms", data={"agree": "y"})

        version = seeded_terms[TermsAudience.TEACHER]
        assert terms_service.has_accepted(unaccepted_teacher, version)
        acceptance = version.acceptances[0]
        assert acceptance.user_id == unaccepted_teacher.id
        assert acceptance.accepted_at is not None

    def test_admin_and_assistant_are_never_gated(self, app, seeded_terms, db):
        for role, phone in [(Role.ADMIN, "+201012343333"), (Role.ASSISTANT, "+201012344444")]:
            staff = make_user(role, phone=phone)
            client = app.test_client()
            login(client, staff)
            assert terms_service.needs_acceptance(staff) is False
            response = client.get("/", follow_redirects=False)
            assert response.status_code == 302
            assert "/terms" not in response.headers["Location"]


class TestTermsVersioning:
    def test_publishing_v2_re_prompts_users_who_accepted_v1(self, app, db, seeded_terms):
        """The requirement the brief asked for: bump the version, re-prompt.

        Nothing about the *user* changes here — only a new current version
        exists, and the gate is a query rather than a flag.
        """
        teacher = make_user(Role.TEACHER, phone="+201012345555")
        accept_current_terms(teacher)
        assert terms_service.needs_acceptance(teacher) is False

        terms_service.publish_version(None, TermsAudience.TEACHER, body_ar="نسخة جديدة من الشروط")

        assert terms_service.needs_acceptance(teacher) is True

    def test_the_old_acceptance_record_survives(self, app, db, seeded_terms):
        teacher = make_user(Role.TEACHER, phone="+201012346666")
        accept_current_terms(teacher)
        v1 = seeded_terms[TermsAudience.TEACHER]

        terms_service.publish_version(None, TermsAudience.TEACHER, body_ar="v2")

        # The v1 acceptance stays on file — needed if the terms are disputed.
        assert terms_service.has_accepted(teacher, v1) is True

    def test_version_numbers_increment_per_audience(self, app, db, seeded_terms):
        v2 = terms_service.publish_version(None, TermsAudience.TEACHER, body_ar="v2")
        assert v2.version == 2
        # The other audience is untouched.
        student_current = terms_service.current_version_for(
            make_user(Role.STUDENT, phone="+201012347777")
        )
        assert student_current.version == 1
