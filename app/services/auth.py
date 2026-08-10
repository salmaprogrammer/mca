"""Credentials: phone normalisation, password generation, hashing, login (S1.1).

Ground rule 4 (PLAN.md §7): a generated plaintext password is returned to the
caller exactly once, so it can be rendered in the creation response. It is
never stored, logged, flashed, or put in a session.
"""

from __future__ import annotations

import secrets

import phonenumbers
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from flask import current_app
from sqlalchemy import select

from app.extensions import db
from app.models.user import User

# Ambiguous glyphs removed (no 0, O, 1, I, l) — these get read aloud over the
# phone to families, so 31 unmistakable characters beats 62 confusing ones.
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

_hasher = PasswordHasher()


class PhoneNumberError(ValueError):
    """Raised when a phone number cannot be parsed or is not a valid number."""


def normalise_phone(raw: str | None, region: str | None = None) -> str:
    """Accept `01xxxxxxxxx` or `+201xxxxxxxxx`; always store E.164.

    Login normalises before lookup too, so either form works at the login box.
    """
    if not raw or not raw.strip():
        raise PhoneNumberError("empty")
    region = region or current_app.config.get("PHONE_DEFAULT_REGION", "EG")
    try:
        parsed = phonenumbers.parse(raw.strip(), region)
    except phonenumbers.NumberParseException as exc:
        raise PhoneNumberError(str(exc)) from exc
    if not phonenumbers.is_valid_number(parsed):
        raise PhoneNumberError("invalid")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def generate_password(length: int | None = None) -> str:
    length = length or current_app.config.get("GENERATED_PASSWORD_LENGTH", 10)
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def hash_password(plaintext: str) -> str:
    return _hasher.hash(plaintext)


def verify_password(password_hash: str | None, plaintext: str) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, plaintext)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def find_by_login(identifier: str) -> User | None:
    """Look a user up by phone in either accepted format."""
    if not identifier:
        return None
    candidates = [identifier.strip()]
    try:
        candidates.append(normalise_phone(identifier))
    except PhoneNumberError:
        pass
    return db.session.scalar(select(User).where(User.username.in_(candidates)))


def authenticate(identifier: str, plaintext: str) -> User | None:
    """Return the user on success, None on any failure.

    Callers must render an identical response for every None case — an unknown
    phone and a wrong password must be indistinguishable (sprint S1.2).
    """
    user = find_by_login(identifier)
    if user is None:
        # Spend the same work as a real verification so timing doesn't leak
        # whether the account exists.
        _hasher.hash("timing-equaliser")
        return None
    if not user.is_active or not user.password_hash:
        return None
    if not verify_password(user.password_hash, plaintext):
        return None
    return user


def set_password(user: User, plaintext: str, *, must_change: bool = False) -> None:
    user.password_hash = hash_password(plaintext)
    user.must_change_password = must_change
