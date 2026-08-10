"""Account creation flows and credential lifecycle (sprint S1.6).

Every function here takes the acting user first and writes an audit row.
Generated plaintext passwords are returned in the `NewAccount` result and are
the caller's single opportunity to display them (ground rule 4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flask import current_app
from sqlalchemy import select

from app.extensions import db
from app.models.enums import Role
from app.models.user import ParentLink, StudentProfile, TeacherProfile, User
from app.services import audit
from app.services.auth import generate_password, hash_password, normalise_phone


class AccountError(ValueError):
    """A creation attempt that the operator needs to see explained."""


@dataclass
class NewAccount:
    user: User
    plaintext_password: str | None
    label: str

    @property
    def has_login(self) -> bool:
        return self.plaintext_password is not None


@dataclass
class CreationResult:
    """What to show on the one-time credential screen."""

    accounts: list[NewAccount] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


def _phone_owner(phone: str) -> User | None:
    return db.session.scalar(select(User).where(User.phone == phone))


def _create_user(
    *,
    actor: User,
    role: Role,
    full_name: str,
    phone: str | None,
    with_login: bool = True,
) -> tuple[User, str | None]:
    plaintext = generate_password() if with_login else None
    user = User(
        role=role,
        full_name=full_name.strip(),
        phone=phone,
        username=phone if with_login else None,
        password_hash=hash_password(plaintext) if plaintext else None,
        must_change_password=bool(plaintext),
        created_by_id=actor.id,
    )
    db.session.add(user)
    db.session.flush()
    audit.record(
        "account.create",
        "user",
        entity_id=user.id,
        actor=actor,
        after=audit.snapshot(user),
    )
    return user, plaintext


# ---------------------------------------------------------------- assistants


def create_assistant(actor: User, full_name: str, phone: str) -> CreationResult:
    """Admin-only (brief: only the admin creates assistant accounts)."""
    normalised = normalise_phone(phone)
    if _phone_owner(normalised):
        raise AccountError(f"The number {phone} already belongs to another account.")
    user, plaintext = _create_user(
        actor=actor, role=Role.ASSISTANT, full_name=full_name, phone=normalised
    )
    db.session.commit()
    return CreationResult(accounts=[NewAccount(user, plaintext, "Assistant")])


# ------------------------------------------------------------------ teachers


def create_teacher(
    actor: User, full_name: str, phone: str, subject: str | None = None
) -> CreationResult:
    normalised = normalise_phone(phone)
    if _phone_owner(normalised):
        raise AccountError(f"The number {phone} already belongs to another account.")
    user, plaintext = _create_user(
        actor=actor, role=Role.TEACHER, full_name=full_name, phone=normalised
    )
    db.session.add(TeacherProfile(user_id=user.id, subject=subject))
    db.session.commit()
    return CreationResult(accounts=[NewAccount(user, plaintext, "Teacher")])


# ------------------------------------------------------- students + parents


def create_student_with_parent(
    actor: User,
    *,
    student_name: str,
    student_phone: str | None = None,
    parent_name: str | None = None,
    parent_phone: str | None = None,
    school: str | None = None,
    grade: str | None = None,
) -> CreationResult:
    """Create a student and, in the same transaction, create or link a parent.

    Three cases, per the brief plus OPEN QUESTION 1:

    1. Distinct student and parent phones -> two logins.
    2. One phone only (or both the same) -> parent login, student without one.
       The student is still a full record; the family uses the parent account.
       Controlled by config FAMILY_SHARED_PHONE_MODE ("parent_only" default,
       "require_distinct" refuses instead).
    3. The parent phone already belongs to an existing parent -> link the new
       child to them rather than creating a duplicate parent.
    """
    result = CreationResult()

    student_e164 = normalise_phone(student_phone) if student_phone else None
    parent_e164 = normalise_phone(parent_phone) if parent_phone else None

    if student_e164 and parent_e164 and student_e164 == parent_e164:
        parent_e164, student_e164 = parent_e164, None
    if not parent_e164 and student_e164:
        # Only one number supplied and it came in the student field.
        mode = current_app.config.get("FAMILY_SHARED_PHONE_MODE", "parent_only")
        if mode == "parent_only":
            parent_e164, student_e164 = student_e164, None
    if not parent_e164:
        raise AccountError("A parent phone number is required to create the family account.")

    shared_phone_mode = current_app.config.get("FAMILY_SHARED_PHONE_MODE")
    if shared_phone_mode == "require_distinct" and not student_e164:
        raise AccountError("Student and parent must each have their own phone number.")

    # --- resolve the parent -------------------------------------------------
    existing = _phone_owner(parent_e164)
    parent: User
    if existing is None:
        parent, parent_plaintext = _create_user(
            actor=actor,
            role=Role.PARENT,
            full_name=(parent_name or f"{student_name} — parent").strip(),
            phone=parent_e164,
        )
        result.accounts.append(NewAccount(parent, parent_plaintext, "Parent"))
    elif existing.role is Role.PARENT:
        parent = existing
        result.notices.append(
            f"{parent.full_name} already has a parent account — the new student was "
            f"linked to it, and their existing password still works."
        )
    else:
        raise AccountError(
            f"The number {parent_phone} already belongs to a "
            f"{existing.role.value} account ({existing.full_name}). "
            "Use a different number for the parent."
        )

    # --- the student --------------------------------------------------------
    if student_e164 and _phone_owner(student_e164):
        raise AccountError(f"The number {student_phone} already belongs to another account.")

    student, student_plaintext = _create_user(
        actor=actor,
        role=Role.STUDENT,
        full_name=student_name,
        phone=student_e164,
        with_login=bool(student_e164),
    )
    db.session.add(StudentProfile(user_id=student.id, school=school, grade=grade))

    link = ParentLink(parent_id=parent.id, student_id=student.id)
    db.session.add(link)
    db.session.flush()
    audit.record(
        "parent_link.create",
        "parent_link",
        entity_id=link.id,
        actor=actor,
        after={"parent_id": parent.id, "student_id": student.id},
    )

    result.accounts.append(NewAccount(student, student_plaintext, "Student"))
    if not student_e164:
        result.notices.append(
            f"{student.full_name} has no separate login — the family signs in with the "
            f"parent account. Add a phone number for them later to enable their own access."
        )

    db.session.commit()
    return result


# ------------------------------------------------------ credential lifecycle


def regenerate_password(actor: User, user: User) -> str:
    """Issue a fresh password after the one-time reveal is gone.

    Not in the original brief, and the system is unusable without it: the
    plaintext is genuinely unrecoverable, so an assistant who closed the tab
    would otherwise have locked the family out permanently (PLAN.md §2.4).
    """
    if not user.username:
        raise AccountError(f"{user.full_name} has no login to reset.")
    plaintext = generate_password()
    user.password_hash = hash_password(plaintext)
    user.must_change_password = True
    audit.record(
        "account.password_regenerated",
        "user",
        entity_id=user.id,
        actor=actor,
        after={"must_change_password": True},
    )
    db.session.commit()
    return plaintext


def set_active(actor: User, user: User, active: bool) -> None:
    before = audit.snapshot(user, ["is_active"])
    user.is_active = active
    audit.record(
        "account.activated" if active else "account.deactivated",
        "user",
        entity_id=user.id,
        actor=actor,
        before=before,
        after=audit.snapshot(user, ["is_active"]),
    )
    db.session.commit()
