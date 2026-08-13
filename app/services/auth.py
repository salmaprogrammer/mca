"""Credentials: phone normalisation, password generation, hashing, login (S1.1).

Ground rule 4 (PLAN.md §7): a generated plaintext password is returned to the
caller exactly once, so it can be rendered in the creation response. It is
never stored, logged, flashed, or put in a session.
"""

from __future__ import annotations

import re
import secrets

import phonenumbers
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from flask import current_app
from sqlalchemy import or_, select

from app.extensions import db
from app.models.user import User

# A username is a text handle an admin may set on any account so the family
# can sign in with something they'll remember (e.g. "sara.h") instead of
# their phone number. Kept strict on purpose: no whitespace, no @, no
# leading dot; digits-only is rejected so it can never collide with a
# phone-shaped identifier and confuse the login lookup below.
USERNAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{2,23}$")


class UsernameError(ValueError):
    """A rejected admin-supplied username with a reason the operator can act on."""


def normalise_username(raw: str | None) -> str | None:
    """Return the canonical form (lower-cased) or None if the input is empty."""
    if not raw or not raw.strip():
        return None
    candidate = raw.strip()
    if not USERNAME_PATTERN.fullmatch(candidate):
        raise UsernameError(
            "A username must start with a letter and be 3–24 characters of "
            "letters, digits, dot, underscore or hyphen."
        )
    return candidate.lower()

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
    """Match a login attempt against username OR phone.

    A user may type any of these and land on their own account:
      - their phone in either accepted format (01xxxxxxxxx / +201xxxxxxxxx),
      - the E.164 form stored on the row,
      - an admin-set text username (case-insensitive).

    Historically `username` mirrored the E.164 phone; new accounts still do
    that unless an admin sets a distinct username. Both columns are unique,
    so the OR-match cannot return more than one row.
    """
    if not identifier or not identifier.strip():
        return None
    raw = identifier.strip()
    candidates = {raw, raw.lower()}
    try:
        candidates.add(normalise_phone(raw))
    except PhoneNumberError:
        pass
    return db.session.scalar(
        select(User).where(
            or_(User.username.in_(candidates), User.phone.in_(candidates))
        )
    )


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
