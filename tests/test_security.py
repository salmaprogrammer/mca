"""Cross-cutting protections that the ordinary test config relaxes.

TestConfig turns CSRF off so form tests stay readable. That makes it possible
to ship a form with no token and never notice, so CSRF gets its own app here
with protection switched back on.
"""

from __future__ import annotations

import pytest

from app import create_app
from app.extensions import db as _db
from app.models.enums import Role
from tests.conftest import KNOWN_PASSWORD, _clear_per_request_caches, make_user


@pytest.fixture
def csrf_app():
    application = create_app("testing")
    application.config["WTF_CSRF_ENABLED"] = True
    application.before_request_funcs.setdefault(None, []).insert(0, _clear_per_request_caches)
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


class TestCsrf:
    def test_login_without_a_token_is_rejected(self, csrf_app):
        make_user(Role.ADMIN, phone="+201000000001")
        client = csrf_app.test_client()
        response = client.post(
            "/login", data={"identifier": "+201000000001", "password": KNOWN_PASSWORD}
        )
        assert response.status_code == 400

    def test_login_with_a_token_succeeds(self, csrf_app):
        make_user(Role.ADMIN, phone="+201000000001")
        client = csrf_app.test_client()

        page = client.get("/login").get_data(as_text=True)
        token = page.split('name="csrf_token" type="hidden" value="')[1].split('"')[0]

        response = client.post(
            "/login",
            data={
                "identifier": "+201000000001",
                "password": KNOWN_PASSWORD,
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    def test_state_changing_post_without_a_token_is_rejected(self, csrf_app):
        """Language switching writes to the session, so it needs a token too."""
        client = csrf_app.test_client()
        assert client.post("/me/language", data={"locale": "en"}).status_code == 400


class TestSessionCookie:
    def test_production_config_demands_secure_cookies(self, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "x" * 40)
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
        from app.config import ProdConfig

        cfg = ProdConfig()
        assert cfg.SESSION_COOKIE_SECURE is True
        assert cfg.SESSION_COOKIE_HTTPONLY is True
        assert cfg.SESSION_COOKIE_SAMESITE == "Lax"

    def test_production_refuses_to_boot_without_a_secret(self, monkeypatch):
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from app.config import ProdConfig

        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            ProdConfig()


class TestLocalisationEndToEnd:
    def test_arabic_strings_actually_render(self, app, client):
        """Proves the compiled catalogue is wired up, not just present.

        If `pybabel compile` is skipped, gettext silently falls back to the
        English msgid and every screen looks fine in tests while being wrong
        for every real user.
        """
        body = client.get("/login").get_data(as_text=True)
        assert "تسجيل الدخول" in body  # "Sign in"
        assert "Sign in with your phone number" not in body

    def test_english_falls_back_to_the_source_strings(self, app, client):
        client.post("/me/language", data={"locale": "en", "next": "/login"})
        body = client.get("/login").get_data(as_text=True)
        assert "Sign in" in body
        assert "تسجيل الدخول" not in body

    def test_the_login_required_prompt_is_translated(self, app, client):
        """Flask-Login's own default message is untranslated English.

        It is easy to miss because it only appears on the redirect from a
        protected page, which the happy-path tests never hit.
        """
        body = client.get("/admin/", follow_redirects=True).get_data(as_text=True)
        assert "برجاء تسجيل الدخول للمتابعة." in body
        assert "Please log in to access this page" not in body


class TestSessionProtection:
    def test_a_changed_user_agent_does_not_sign_the_user_out(self, app, admin):
        """Guards the deliberate choice of "basic" over "strong".

        Strong protection wipes the session when the IP or user agent changes.
        Parents on mobile data switching between wifi and 4G would be signed
        out mid-visit, so this asserts the session survives.
        """
        from tests.conftest import login

        client = app.test_client()
        login(client, admin)
        assert client.get("/admin/").status_code == 200

        moved = client.get("/admin/", headers={"User-Agent": "a-different-phone/2.0"})
        assert moved.status_code == 200

    def test_terms_gate_is_rtl_even_when_the_ui_is_english(self, app, db, seeded_terms):
        """The terms body has no English translation yet (open question 6).

        It must keep rendering right-to-left rather than inheriting the LTR
        page direction, or the Arabic text becomes unreadable.
        """
        from tests.conftest import login

        teacher = make_user(Role.TEACHER, phone="+201011114444")
        client = app.test_client()
        login(client, teacher)
        client.post("/me/language", data={"locale": "en", "next": "/terms"})

        body = client.get("/terms").get_data(as_text=True)
        assert 'class="terms-panel" dir="rtl"' in body


class TestGRuleCompliance:
    def test_no_template_disables_autoescaping(self):
        """Ground rule: no `|safe` on anything user-supplied."""
        from pathlib import Path

        templates = Path(__file__).resolve().parent.parent / "app" / "templates"
        offenders = [
            path.relative_to(templates)
            for path in templates.rglob("*.html")
            if "|safe" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_identity_does_not_leak_between_clients(self, app, db, seeded_terms):
        """Guards the conftest cache-clearing hook itself.

        Without it, the first request's user stays cached on the shared app
        context and every later request in the test is silently that user —
        which would make the whole authorization suite pass for the wrong
        reason. Here an admin logs in first, then a teacher; if identity
        leaked, the teacher's client would reach the admin area.
        """
        from tests.conftest import login

        admin = make_user(Role.ADMIN, phone="+201000000001")
        teacher = make_user(Role.TEACHER, phone="+201011115555")
        from tests.conftest import accept_current_terms

        accept_current_terms(teacher)

        admin_client = app.test_client()
        login(admin_client, admin)
        assert admin_client.get("/admin/").status_code == 200

        teacher_client = app.test_client()
        login(teacher_client, teacher)
        assert teacher_client.get("/admin/").status_code == 403
        assert teacher_client.get("/teacher/").status_code == 200

        # And the admin's own client is unaffected by the second login.
        assert admin_client.get("/admin/").status_code == 200
