"""Course catalogue business rules (sprints S2.1, S2.2, S2.3, S2.6).

Every function takes the acting user first and writes an audit row.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal

from flask_babel import gettext as _
from sqlalchemy import select
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models.course import Course, CourseSlot, CourseType
from app.models.enums import CourseStatus, Role
from app.models.user import User
from app.seeds.course_types import COURSE_TYPES
from app.services import audit, storage
from app.services.scheduling import ProposedSlot, assert_no_conflicts


class CourseError(ValueError):
    """Something the operator needs explained."""


# ------------------------------------------------------------ course types


def seed_course_types() -> int:
    """Insert the six fixed types if absent. Idempotent; safe on every deploy."""
    created = 0
    for spec in COURSE_TYPES:
        existing = db.session.get(CourseType, spec["id"])
        if existing:
            continue
        db.session.add(CourseType(**spec))
        created += 1
    db.session.commit()
    return created


def all_course_types() -> list[CourseType]:
    return list(db.session.scalars(select(CourseType).order_by(CourseType.id)))


# ---------------------------------------------------------------- courses


def _slots_from_input(raw_slots: list[dict], duration: int) -> list[ProposedSlot]:
    proposed = []
    for entry in raw_slots:
        weekday = entry.get("weekday")
        start = entry.get("start_time")
        if weekday is None or start is None:
            continue
        proposed.append(
            ProposedSlot(
                weekday=int(weekday),
                start_time=start if isinstance(start, time) else _parse_time(start),
                duration_minutes=int(entry.get("duration_minutes") or duration),
            )
        )
    return proposed


def _parse_time(value: str) -> time:
    try:
        hour, minute = value.split(":")[:2]
        return time(hour=int(hour), minute=int(minute))
    except (ValueError, AttributeError) as exc:
        raise CourseError(_("That is not a valid time of day.")) from exc


def _require_teacher(teacher_id: int) -> User:
    teacher = db.session.get(User, teacher_id) if teacher_id else None
    if teacher is None or teacher.role is not Role.TEACHER:
        raise CourseError(_("Choose a teacher for this course."))
    return teacher


def create_course(
    actor: User,
    *,
    name: str,
    course_type_id: int,
    teacher_id: int,
    slots: list[dict],
    description: str | None = None,
    price_egp: Decimal | float | str = 0,
    trial_enabled: bool = False,
    start_date: date | None = None,
    cover_image: FileStorage | None = None,
    duration_minutes: int = 90,
) -> Course:
    course_type = db.session.get(CourseType, int(course_type_id)) if course_type_id else None
    if course_type is None:
        raise CourseError(_("Choose a course type."))
    _require_teacher(teacher_id)

    proposed = _slots_from_input(slots, duration_minutes)
    if len(proposed) != course_type.sessions_per_week:
        raise CourseError(
            _(
                "%(type)s needs exactly %(expected)d session(s) per week; "
                "%(given)d were given.",
                type=course_type.label_en,
                expected=course_type.sessions_per_week,
                given=len(proposed),
            )
        )

    # Raises ScheduleConflictError, which the route renders verbatim.
    assert_no_conflicts(teacher_id, proposed)

    course = Course(
        name=name.strip(),
        course_type_id=course_type.id,
        teacher_id=teacher_id,
        description=(description or "").strip() or None,
        price_egp=Decimal(str(price_egp or 0)),
        trial_enabled=bool(trial_enabled),
        start_date=start_date,
        status=CourseStatus.ACTIVE,
        created_by_id=actor.id,
    )
    if cover_image and cover_image.filename:
        course.cover_image_path = storage.save_image(cover_image)

    db.session.add(course)
    db.session.flush()

    for slot in proposed:
        db.session.add(
            CourseSlot(
                course_id=course.id,
                weekday=slot.weekday,
                start_time=slot.start_time,
                duration_minutes=slot.duration_minutes,
            )
        )

    db.session.flush()
    audit.record(
        "course.create", "course", entity_id=course.id, actor=actor, after=audit.snapshot(course)
    )
    db.session.commit()
    return course


def update_course(
    actor: User,
    course: Course,
    *,
    name: str,
    course_type_id: int,
    teacher_id: int,
    slots: list[dict],
    description: str | None = None,
    price_egp: Decimal | float | str = 0,
    trial_enabled: bool = False,
    start_date: date | None = None,
    cover_image: FileStorage | None = None,
    duration_minutes: int = 90,
) -> Course:
    """Edits re-run the conflict check — including teacher reassignment.

    The brief calls this out specifically: validating only on creation leaves
    an obvious hole, since moving a course to a busy teacher is exactly how a
    double-booking gets made.
    """
    before = audit.snapshot(course)

    course_type = db.session.get(CourseType, int(course_type_id)) if course_type_id else None
    if course_type is None:
        raise CourseError(_("Choose a course type."))
    _require_teacher(teacher_id)

    proposed = _slots_from_input(slots, duration_minutes)
    if len(proposed) != course_type.sessions_per_week:
        raise CourseError(
            _(
                "%(type)s needs exactly %(expected)d session(s) per week; "
                "%(given)d were given.",
                type=course_type.label_en,
                expected=course_type.sessions_per_week,
                given=len(proposed),
            )
        )

    assert_no_conflicts(teacher_id, proposed, exclude_course_id=course.id)

    course.name = name.strip()
    course.course_type_id = course_type.id
    course.teacher_id = teacher_id
    course.description = (description or "").strip() or None
    course.price_egp = Decimal(str(price_egp or 0))
    course.trial_enabled = bool(trial_enabled)
    course.start_date = start_date

    if cover_image and cover_image.filename:
        old = course.cover_image_path
        course.cover_image_path = storage.save_image(cover_image)
        if old:
            storage.delete(old)

    course.slots.clear()
    db.session.flush()
    for slot in proposed:
        db.session.add(
            CourseSlot(
                course_id=course.id,
                weekday=slot.weekday,
                start_time=slot.start_time,
                duration_minutes=slot.duration_minutes,
            )
        )

    db.session.flush()
    audit.record(
        "course.update",
        "course",
        entity_id=course.id,
        actor=actor,
        before=before,
        after=audit.snapshot(course),
    )
    db.session.commit()
    return course


def archive_course(actor: User, course: Course) -> None:
    before = audit.snapshot(course, ["status"])
    course.status = CourseStatus.ARCHIVED
    audit.record(
        "course.archive",
        "course",
        entity_id=course.id,
        actor=actor,
        before=before,
        after=audit.snapshot(course, ["status"]),
    )
    db.session.commit()
