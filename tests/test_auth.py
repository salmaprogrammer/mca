"""Sprints S1.1 and S1.2 — credentials and login."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.enums import Role
from app.models.user import User
from app.services.auth import (
    PhoneNumberError,
    authenticate,
    generate_password,
    normalise_phone,
)
from tests.conftest import KNOWN_PASSWORD, login, make_user


class TestPhoneNormalisation:
    def test_local_and_international_agree(self, app):
        assert normalise_phone("01011112222") == normalise_phone("+201011112222")
        assert normalise_phone("01011112222") == "+201011112222"

    def test_whitespace_tolerated(self, app):
        assert normalise_phone("  01011112222  ") == "+201011112222"

    @pytest.mark.parametrize("bad", ["", "   ", "abc", "12", "0000"])
    def test_invalid_rejected(self, app, bad):
        with pytest.raises(PhoneNumberError):
            normalise_phone(bad)


class TestGeneratedPasswords:
    def test_length_and_alphabet(self, app):
        password = generate_password()
        assert len(password) == app.config["GENERATED_PASSWORD_LENGTH"]
        # Ambiguous glyphs are read aloud to families; they must never appear.
        assert not (set(password) & set("01OIl"))

    def test_not_repeated(self, app):
        assert len({generate_password() for _ in range(50)}) > 45


class TestAuthenticate:
    def test_accepts_either_phone_format(self, app, admin):
        assert authenticate("+201000000001", KNOWN_PASSWORD) is not None
        assert authenticate("01000000001", KNOWN_PASSWORD) is not None

    def test_wrong_password_fails(self, app, admin):
        assert authenticate("+201000000001", "wrong") is None

    def test_unknown_phone_fails(self, app, admin):
        assert authenticate("+201999999999", KNOWN_PASSWORD) is None

    def test_deactivated_account_cannot_log_in(self, app, db):
        user = make_user(Role.ADMIN, phone="+201000000009", is_active=False)
        assert authenticate(user.username, KNOWN_PASSWORD) is None

    def test_account_without_password_cannot_log_in(self, app, db):
        user = make_user(Role.STUDENT, phone="+201044445555", password=None)
        assert user.username is None
        assert authenticate("+201044445555", KNOWN_PASSWORD) is None


class TestLoginRoute:
    def test_successful_login_redirects_to_role_home(self, client, admin):
        response = login(client, admin)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")

    def test_failure_modes_are_indistinguishable(self, client, admin):
        """An unknown phone and a wrong password must look identical.

        Otherwise the login form becomes a phone-number oracle: an attacker
        submits numbers and reads off which ones belong to real accounts.

        The form legitimately echoes back whatever was typed, so that one
        value is normalised out before comparing — it is the attacker's own
        input and tells them nothing they did not already know.
        """
        known, unknown = admin.username, "+201999999999"

        wrong_password = client.post("/login", data={"identifier": known, "password": "wrong"})
        unknown_phone = client.post("/login", data={"identifier": unknown, "password": "wrong"})

        assert wrong_password.status_code == unknown_phone.status_code == 401
        assert wrong_password.get_data(as_text=True).replace(known, "@") == (
            unknown_phone.get_data(as_text=True).replace(unknown, "@")
        )

    def test_failed_login_is_audited(self, client, db, admin):
        client.post("/login", data={"identifier": admin.username, "password": "wrong"})
        entry = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "auth.login_failed")
        )
        assert entry is not None
        # The attempted identifier is never recorded, only that one was supplied.
        assert entry.after_json == {"identifier_supplied": True}

    def test_logout_clears_the_session(self, as_admin):
        as_admin.post("/logout")
        assert as_admin.get("/", follow_redirects=False).status_code == 302


class TestPasswordStorage:
    def test_plaintext_never_reaches_the_database(self, app, db, admin):
        stored = db.session.scalar(select(User).where(User.id == admin.id))
        assert stored.password_hash != KNOWN_PASSWORD
        assert stored.password_hash.startswith("$argon2")

    def test_repr_leaks_nothing(self, app, admin):
        text = repr(admin)
        assert admin.phone not in text
        assert "argon2" not in text
