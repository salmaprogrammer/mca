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
from app.models.user import AssistantProfile, ParentLink, StudentProfile, TeacherProfile, User
from app.services import audit
from app.services.auth import (
    UsernameError,
    generate_password,
    hash_password,
    normalise_phone,
    normalise_username,
)


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


def _username_owner(username: str) -> User | None:
    return db.session.scalar(select(User).where(User.username == username))


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


def create_assistant(
    actor: User, full_name: str, phone: str, title: str | None = None
) -> CreationResult:
    """Admin-only (brief: only the admin creates assistant accounts).

    `title` is purely a display label (e.g. "Academic Manager") — it never
    changes the account's actual permissions, which stay Role.ASSISTANT.
    """
    normalised = normalise_phone(phone)
    if _phone_owner(normalised):
        raise AccountError(f"The number {phone} already belongs to another account.")
    user, plaintext = _create_user(
        actor=actor, role=Role.ASSISTANT, full_name=full_name, phone=normalised
    )
    cleaned_title = (title or "").strip() or None
    if cleaned_title:
        db.session.add(AssistantProfile(user_id=user.id, title=cleaned_title))
    db.session.commit()
    return CreationResult(accounts=[NewAccount(user, plaintext, cleaned_title or "Assistant")])


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

    # Refuse a second student with the same trimmed name already linked to this
    # parent. Real siblings do exist, but they never share a name; what this
    # actually catches is a double-submit or a browser refresh replaying the
    # POST, which would otherwise create two identical rows because a
    # parent-only family student has no phone to collide on.
    name_key = " ".join(student_name.split()).casefold()
    for existing_child in (link.student for link in parent.children_links):
        if " ".join(existing_child.full_name.split()).casefold() == name_key:
            raise AccountError(
                f"{parent.full_name} already has a student named "
                f"{student_name.strip()}. If this is a second child with the "
                "same name, distinguish them (for example 'Omar A' / 'Omar B'). "
                "Otherwise the first submission created the student already."
            )

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


# ---------------------------------------------------------- profile updates


_UNSET = object()


def update_user_profile(
    actor: User,
    user: User,
    *,
    full_name: str | None = None,
    phone: object = _UNSET,
    username: object = _UNSET,
    subject: object = _UNSET,
    school: object = _UNSET,
    grade: object = _UNSET,
) -> None:
    """Edit an existing account's identity + role-profile fields.

    `phone`, `username`, `subject`, `school`, `grade` use the `_UNSET` sentinel:
    - omitted (`_UNSET`) means "leave alone",
    - passed as an empty string or None means "clear it".

    The distinction matters for phone: clearing it removes the login (per the
    users table check constraint), which is a legitimate operation but not one
    that should happen accidentally because a form field was left blank.
    """
    before = audit.snapshot(user)

    if full_name is not None:
        cleaned = " ".join(full_name.split())
        if not cleaned:
            raise AccountError("Name cannot be blank.")
        user.full_name = cleaned

    if phone is not _UNSET:
        raw = (phone or "").strip() if phone else ""
        if raw:
            new_e164 = normalise_phone(raw)
            if new_e164 != user.phone:
                owner = _phone_owner(new_e164)
                if owner and owner.id != user.id:
                    raise AccountError(
                        f"The number {raw} already belongs to another account."
                    )
                # If the current username was the phone (the default) keep them
                # in sync; a custom username set later stays untouched.
                if user.username == user.phone:
                    user.username = new_e164
                user.phone = new_e164
        else:
            # Clearing the phone removes the login entirely to satisfy the
            # ck_users_login_needs_password check constraint.
            user.phone = None
            if user.username == before.get("phone"):
                user.username = None
                user.password_hash = None
                user.must_change_password = False

    if username is not _UNSET:
        cleaned = normalise_username(username) if username else None
        if cleaned is None:
            # Reverting to phone-as-username, if there is still a phone.
            user.username = user.phone
            if not user.username:
                user.password_hash = None
                user.must_change_password = False
        else:
            owner = _username_owner(cleaned)
            if owner and owner.id != user.id:
                raise AccountError(
                    f"The username '{cleaned}' is already taken."
                )
            if not user.password_hash:
                raise AccountError(
                    "This account has no password yet. Add a phone number "
                    "and issue a password before assigning a username."
                )
            user.username = cleaned

    if subject is not _UNSET and user.role is Role.TEACHER:
        value = subject.strip() if subject else None
        if user.teacher_profile:
            user.teacher_profile.subject = value or None
        else:
            db.session.add(TeacherProfile(user_id=user.id, subject=value or None))

    if school is not _UNSET and user.role is Role.STUDENT:
        value = school.strip() if school else None
        if user.student_profile:
            user.student_profile.school = value or None
        else:
            db.session.add(StudentProfile(user_id=user.id, school=value or None))

    if grade is not _UNSET and user.role is Role.STUDENT:
        value = grade.strip() if grade else None
        if user.student_profile:
            user.student_profile.grade = value or None
        else:
            db.session.add(StudentProfile(user_id=user.id, grade=value or None))

    audit.record(
        "account.updated",
        "user",
        entity_id=user.id,
        actor=actor,
        before=before,
        after=audit.snapshot(user),
    )
    db.session.commit()


# ------------------------------------------------------------- safe delete


def _delete_blockers(user: User) -> list[str]:
    """Reasons a hard delete would destroy real history.

    Called by delete_user_safely to decide between hard delete (freshly-created
    duplicate with no activity) and soft deactivate (a real person who has been
    used in enrollments, sessions, etc.).
    """
    from app.models.course import Course, Enrollment
    from app.models.session import AttendanceRecord, CourseSession
    from app.models.teaching import Feedback, Homework, Material

    def any_row(model, field, value):
        return db.session.scalar(
            select(model.id).where(field == value).limit(1)
        ) is not None

    blockers: list[str] = []
    if user.role is Role.STUDENT:
        if any_row(Enrollment, Enrollment.student_id, user.id):
            blockers.append("enrollments")
        if any_row(AttendanceRecord, AttendanceRecord.student_id, user.id):
            blockers.append("attendance")
        if any_row(Feedback, Feedback.student_id, user.id):
            blockers.append("feedback")
    if user.role is Role.TEACHER:
        if any_row(Course, Course.teacher_id, user.id):
            blockers.append("courses")
        if any_row(CourseSession, CourseSession.teacher_id, user.id):
            blockers.append("sessions")
        if any_row(Homework, Homework.created_by_id, user.id):
            blockers.append("homework authored")
        if any_row(Material, Material.created_by_id, user.id):
            blockers.append("materials authored")
        if any_row(Feedback, Feedback.created_by_id, user.id):
            blockers.append("feedback authored")
    return blockers


def delete_user_safely(actor: User, user: User) -> str:
    """Hard-delete a user only when they have no history; otherwise deactivate.

    Returns "deleted" or "deactivated" so the caller can flash the right
    message. The audit trail records the outcome either way. The row itself
    disappears on hard delete, so a snapshot goes into the audit `before`.
    """
    if user.id == actor.id:
        raise AccountError("You cannot delete your own account.")

    blockers = _delete_blockers(user)
    if blockers:
        set_active(actor, user, False)
        return "deactivated"

    from app.models.audit import AuditLog

    snapshot = audit.snapshot(user)
    audit.record(
        "account.deleted",
        "user",
        entity_id=user.id,
        actor=actor,
        before=snapshot,
    )
    # The nullable back-references have no ON DELETE SET NULL on the schema,
    # so null them by hand before removing the row. Audit rows must survive:
    # the actor becomes "system" (NULL) rather than being deleted with them.
    for audit_row in db.session.scalars(
        select(AuditLog).where(AuditLog.actor_id == user.id)
    ):
        audit_row.actor_id = None
    for other in db.session.scalars(
        select(User).where(User.created_by_id == user.id)
    ):
        other.created_by_id = None
    db.session.delete(user)  # cascades profile + parent_links
    db.session.commit()
    return "deleted"


# --------------------------------------------------- parent contact updates


def set_student_parent_phone(
    actor: User, student: User, phone: str, name: str | None = None
) -> tuple[User, str | None]:
    """Add or update the parent contact number for a student.

    If the student already has a linked parent, that parent's phone (and
    name, if given) is updated. If not, a parent account is created (or an
    existing account with that phone is linked) so the family can be
    reached — this covers the gap where a student was created without a
    parent phone at all.
    """
    if student.role is not Role.STUDENT:
        raise AccountError("Only students have a parent to link.")

    new_e164 = normalise_phone(phone)
    existing_link = student.parent_links[0] if student.parent_links else None

    if existing_link:
        parent = existing_link.parent
        update_user_profile(
            actor, parent, full_name=name if name else None, phone=new_e164
        )
        return parent, None

    owner = _phone_owner(new_e164)
    if owner:
        if owner.role is not Role.PARENT:
            raise AccountError(
                f"The number {phone} already belongs to a {owner.role.value} account."
            )
        parent = owner
        plaintext = None
    else:
        parent, plaintext = _create_user(
            actor=actor,
            role=Role.PARENT,
            full_name=(name or f"{student.full_name} — parent").strip(),
            phone=new_e164,
        )

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
    db.session.commit()
    return parent, plaintext
