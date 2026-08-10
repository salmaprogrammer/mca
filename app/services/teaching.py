"""Homework, feedback and materials (sprints S4.1–S4.3).

Every function takes the acting user first and writes an audit row.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlparse

from flask_babel import gettext as _

from app.extensions import db
from app.models.course import Course
from app.models.enums import Role
from app.models.teaching import Feedback, Homework, Material
from app.models.user import User
from app.services import audit

# Anything else — javascript:, data:, file: — is a way to get a hostile URL in
# front of a student, so the scheme allowlist is deliberately tiny.
ALLOWED_URL_SCHEMES = {"http", "https"}
MAX_URL_LENGTH = 2048


class TeachingError(ValueError):
    """Something the operator needs explained."""


def validate_url(raw: str) -> str:
    """Accept only http(s) links with a host.

    A material link is rendered as an anchor a student will click, so a
    `javascript:` URL here would be stored XSS with extra steps.
    """
    url = (raw or "").strip()
    if not url:
        raise TeachingError(_("Enter a link."))
    if len(url) > MAX_URL_LENGTH:
        raise TeachingError(_("That link is too long."))

    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        raise TeachingError(_("Links must start with http:// or https://"))
    if not parsed.netloc:
        raise TeachingError(_("That does not look like a valid link."))
    return url


def _require_text(raw: str | None) -> str:
    text = (raw or "").strip()
    if not text:
        raise TeachingError(_("Write something before saving."))
    return text


# --------------------------------------------------------------- homework


def add_homework(
    actor: User,
    course: Course,
    *,
    text: str,
    for_date: date | None = None,
    session_id: int | None = None,
) -> Homework:
    homework = Homework(
        course_id=course.id,
        session_id=session_id,
        for_date=for_date or date.today(),
        text=_require_text(text),
        created_by_id=actor.id,
    )
    db.session.add(homework)
    db.session.flush()
    audit.record(
        "homework.create",
        "homework",
        entity_id=homework.id,
        actor=actor,
        after=audit.snapshot(homework),
    )
    db.session.commit()
    return homework


def delete_homework(actor: User, homework: Homework) -> None:
    before = audit.snapshot(homework)
    db.session.delete(homework)
    audit.record(
        "homework.delete",
        "homework",
        entity_id=before.get("id"),
        actor=actor,
        before=before,
    )
    db.session.commit()


# --------------------------------------------------------------- feedback


def add_feedback(
    actor: User,
    course: Course,
    student: User,
    *,
    text: str,
    for_date: date | None = None,
    session_id: int | None = None,
) -> Feedback:
    if student.role is not Role.STUDENT:
        raise TeachingError(_("Feedback can only be written about a student."))

    enrolled = any(e.student_id == student.id for e in course.enrollments)
    if not enrolled:
        raise TeachingError(
            _("%(student)s is not enrolled in this course.", student=student.full_name)
        )

    feedback = Feedback(
        course_id=course.id,
        student_id=student.id,
        session_id=session_id,
        for_date=for_date or date.today(),
        text=_require_text(text),
        created_by_id=actor.id,
    )
    db.session.add(feedback)
    db.session.flush()
    audit.record(
        "feedback.create",
        "feedback",
        entity_id=feedback.id,
        actor=actor,
        after=audit.snapshot(feedback),
    )
    db.session.commit()
    return feedback


def delete_feedback(actor: User, feedback: Feedback) -> None:
    before = audit.snapshot(feedback)
    db.session.delete(feedback)
    audit.record(
        "feedback.delete",
        "feedback",
        entity_id=before.get("id"),
        actor=actor,
        before=before,
    )
    db.session.commit()


# -------------------------------------------------------------- materials


def add_material(actor: User, course: Course, *, title: str, url: str) -> Material:
    material = Material(
        course_id=course.id,
        title=_require_text(title)[:160],
        url=validate_url(url),
        created_by_id=actor.id,
    )
    db.session.add(material)
    db.session.flush()
    audit.record(
        "material.create",
        "material",
        entity_id=material.id,
        actor=actor,
        after=audit.snapshot(material),
    )
    db.session.commit()
    return material


def delete_material(actor: User, material: Material) -> None:
    before = audit.snapshot(material)
    db.session.delete(material)
    audit.record(
        "material.delete",
        "material",
        entity_id=before.get("id"),
        actor=actor,
        before=before,
    )
    db.session.commit()
