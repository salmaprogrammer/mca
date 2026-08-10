"""Sprints S0.1, S0.3, S0.4 — the shell itself."""

from __future__ import annotations

import re
from pathlib import Path

CSS_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "app.css"


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_login_page_renders_anonymously(client):
    assert client.get("/login").status_code == 200


class TestLocalisation:
    def test_default_locale_is_arabic_and_rtl(self, client):
        body = client.get("/login").get_data(as_text=True)
        assert 'lang="ar"' in body
        assert 'dir="rtl"' in body

    def test_switching_to_english_flips_direction(self, client):
        client.post("/login", data={})  # establish a session
        client.post("/me/language", data={"locale": "en", "next": "/login"})
        body = client.get("/login").get_data(as_text=True)
        assert 'lang="en"' in body
        assert 'dir="ltr"' in body

    def test_language_choice_persists_for_a_logged_in_user(self, app, db, as_admin, admin):
        as_admin.post("/me/language", data={"locale": "en", "next": "/"})
        db.session.refresh(admin)
        assert admin.locale == "en"

    def test_unsupported_locale_is_ignored(self, client):
        client.post("/me/language", data={"locale": "fr", "next": "/login"})
        body = client.get("/login").get_data(as_text=True)
        assert 'lang="ar"' in body

    def test_language_switch_cannot_be_used_as_an_open_redirect(self, client):
        response = client.post(
            "/me/language",
            data={"locale": "en", "next": "https://evil.example.com/"},
            follow_redirects=False,
        )
        assert "evil.example.com" not in response.headers["Location"]


class TestStylesheet:
    def test_no_physical_direction_properties(self):
        """Ground rule 5: logical properties only, or Arabic layout breaks.

        Catches `margin-left`, `padding-right`, `border-left`, and bare
        `left:`/`right:` declarations. `text-align: start` is the substitute
        for `text-align: left`.
        """
        css = CSS_PATH.read_text(encoding="utf-8")
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)

        offenders = re.findall(
            r"^\s*((?:margin|padding|border|inset)?-?(?:left|right))\s*:",
            css,
            flags=re.MULTILINE,
        )
        assert offenders == [], f"Use logical properties instead of: {sorted(set(offenders))}"

    def test_no_remote_asset_references(self):
        """Ground rule 6: nothing loads from a CDN."""
        css = CSS_PATH.read_text(encoding="utf-8")
        assert "http://" not in css
        assert "https://" not in css


class TestErrorPages:
    def test_unknown_url_renders_the_404_page(self, as_admin):
        response = as_admin.get("/no/such/page")
        assert response.status_code == 404
        assert "404" in response.get_data(as_text=True)
