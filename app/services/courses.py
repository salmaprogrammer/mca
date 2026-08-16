"""Course catalogue business rules (sprints S2.1, S2.2, S2.3, S2.6).

Every function takes the acting user first and writes an audit row.
"""

from __future__ import annotations

import sqlalchemy as sa

from datetime import date, time
from decimal import Decimal

from flask_babel import gettext as _
from sqlalchemy import select
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models.course import Course, CourseSlot, CourseType, Enrollment
from app.models.enums import BookingStatus, CourseStatus, PaymentStatus, Role
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


def create_course_type(
    actor: User,
    *,
    code: str,
    label_en: str,
    label_ar: str,
    sessions_per_week: int,
    cycle: "CourseCycle",
    total_sessions: int,
    has_own_subject: bool = True,
) -> CourseType:
    """Add a new course type to the catalogue.

    The six original types have hand-assigned IDs (`autoincrement=False`), so
    a new one needs the next free ID computed explicitly rather than left to
    the database.
    """
    code = (code or "").strip().lower().replace(" ", "_")
    label_en = (label_en or "").strip()
    label_ar = (label_ar or "").strip()
    if not code or not label_en or not label_ar:
        raise CourseError(_("Code, English name and Arabic name are all required."))

    existing = db.session.scalar(select(CourseType).where(CourseType.code == code))
    if existing:
        raise CourseError(_("A course type with that code already exists."))

    if sessions_per_week < 1 or total_sessions < 1:
        raise CourseError(_("Sessions per week and total sessions must be at least 1."))

    next_id = (db.session.scalar(select(sa.func.max(CourseType.id))) or 0) + 1

    course_type = CourseType(
        id=next_id,
        code=code,
        label_en=label_en,
        label_ar=label_ar,
        sessions_per_week=sessions_per_week,
        cycle=cycle,
        total_sessions=total_sessions,
        has_own_subject=has_own_subject,
    )
    db.session.add(course_type)
    db.session.flush()
    audit.record(
        "course_type.create",
        "course_type",
        entity_id=course_type.id,
        actor=actor,
        after=audit.snapshot(course_type),
    )
    db.session.commit()
    return course_type


def update_course_type_labels(
    actor: User, course_type: CourseType, label_en: str, label_ar: str
) -> CourseType:
    """Rename a fixed course type's display labels.

    Deliberately narrow: only label_en/label_ar are editable here. Everything
    else about the six fixed types (sessions_per_week, cycle, total_sessions,
    has_own_subject) stays structural and untouchable through the app, per
    the original design note on CourseType.
    """
    label_en = (label_en or "").strip()
    label_ar = (label_ar or "").strip()
    if not label_en or not label_ar:
        raise CourseError(_("Both the English and Arabic names are required."))

    before = audit.snapshot(course_type, ["label_en", "label_ar"])
    course_type.label_en = label_en
    course_type.label_ar = label_ar
    audit.record(
        "course_type.update",
        "course_type",
        entity_id=course_type.id,
        actor=actor,
        before=before,
        after=audit.snapshot(course_type, ["label_en", "label_ar"]),
    )
    db.session.commit()
    return course_type


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
def _course_delete_blockers(course: Course) -> list[str]:
    """Reasons a hard delete would destroy real history."""
    blockers: list[str] = []
    if course.enrollments:
        blockers.append(_("enrolled students"))
    if course.sessions:
        blockers.append(_("sessions"))
    if course.homework:
        blockers.append(_("homework"))
    if course.feedback:
        blockers.append(_("feedback"))
    if course.materials:
        blockers.append(_("materials"))
    return blockers


def delete_course(actor: User, course: Course) -> None:
    """Hard-delete a course unconditionally, regardless of history."""
    before = audit.snapshot(course)
    if course.cover_image_path:
        storage.delete(course.cover_image_path)
    audit.record(
        "course.delete",
        "course",
        entity_id=course.id,
        actor=actor,
        before=before,
    )
    db.session.delete(course)  # cascades slots via relationship cascade
    db.session.commit()


def duplicate_course(actor: User, course: Course, start_date: date | None = None) -> Course:
    """Copy a course's schedule, teacher, cover image and roster into a new
    course instance — for opening a new round of the same course, where only
    the start date actually changes.

    Enrolled students come along, but each starts a fresh, unpaid invoice: a
    new round is a new bill, not a carry-over of the old one's payment state.
    """
    proposed = [
        ProposedSlot(
            weekday=slot.weekday,
            start_time=slot.start_time,
            duration_minutes=slot.duration_minutes,
        )
        for slot in course.slots
    ]
    # Exclude the source course: a deliberate new round keeps the same
    # weekly slots as its predecessor, which is not a real double-booking.
    assert_no_conflicts(course.teacher_id, proposed, exclude_course_id=course.id)

    new_course = Course(
        name=course.name,
        course_type_id=course.course_type_id,
        teacher_id=course.teacher_id,
        description=course.description,
        cover_image_path=course.cover_image_path,
        price_egp=course.price_egp,
        trial_enabled=course.trial_enabled,
        start_date=start_date,
        status=CourseStatus.ACTIVE,
        created_by_id=actor.id,
    )
    db.session.add(new_course)
    db.session.flush()

    for slot in course.slots:
        db.session.add(
            CourseSlot(
                course_id=new_course.id,
                weekday=slot.weekday,
                start_time=slot.start_time,
                duration_minutes=slot.duration_minutes,
            )
        )

    for enrollment in course.enrollments:
        db.session.add(
            Enrollment(
                course_id=new_course.id,
                student_id=enrollment.student_id,
                booking_status=BookingStatus.BOOKED,
                payment_status=PaymentStatus.UNPAID,
                amount_due=new_course.price_egp,
                created_by_id=actor.id,
            )
        )

    db.session.flush()
    audit.record(
        "course.duplicate",
        "course",
        entity_id=new_course.id,
        actor=actor,
        after=audit.snapshot(new_course),
    )
    db.session.commit()
    return new_course
