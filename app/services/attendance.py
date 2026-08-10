"""Student and teacher attendance (sprints S3.3, S3.4, S3.5).

The brief requires date *and* time of check-in, not just present/absent, and
that history be queryable in both directions: per student for the portal, and
per teacher/course for staff.
"""

from __future__ import annotations

from datetime import date, datetime

from flask_babel import gettext as _
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.base import utcnow
from app.models.course import Course, Enrollment
from app.models.enums import AttendanceStatus, Role, SessionStatus
from app.models.session import AttendanceRecord, CourseSession
from app.models.user import User
from app.services import audit


class AttendanceError(ValueError):
    """Something the operator needs explained."""


PRESENT_LIKE = (AttendanceStatus.PRESENT, AttendanceStatus.LATE)


def _assert_markable(session: CourseSession) -> None:
    if session.status is SessionStatus.RESCHEDULED:
        raise AttendanceError(_("This session was moved; mark the replacement instead."))
    if session.is_cancelled:
        raise AttendanceError(_("This session was cancelled, so there is nothing to mark."))


def mark_student(
    actor: User,
    session: CourseSession,
    student: User,
    status: AttendanceStatus,
    *,
    checked_in_at: datetime | None = None,
) -> AttendanceRecord:
    """Record (or correct) one student's attendance at one session.

    Re-marking updates the existing row and audits the change rather than
    inserting a second one — attendance disputes need one authoritative record
    plus its history, not a pile of contradicting rows.
    """
    _assert_markable(session)

    enrolled = db.session.scalar(
        select(Enrollment.id).where(
            Enrollment.course_id == session.course_id,
            Enrollment.student_id == student.id,
        )
    )
    if enrolled is None:
        raise AttendanceError(
            _("%(student)s is not enrolled in this course.", student=student.full_name)
        )

    record = session.attendance_for(student.id)
    before = audit.snapshot(record) if record else None

    if record is None:
        record = AttendanceRecord(session_id=session.id, student_id=student.id)
        db.session.add(record)

    record.status = status
    record.recorded_by_id = actor.id
    record.recorded_at = utcnow()
    # Only a present-like status carries a check-in time; marking someone
    # absent must not stamp them as having arrived.
    record.checked_in_at = (
        (checked_in_at or utcnow()) if status in PRESENT_LIKE else None
    )

    # The first mark on a scheduled session means it actually ran.
    if session.status is SessionStatus.SCHEDULED:
        session.status = SessionStatus.HELD

    db.session.flush()
    audit.record(
        "attendance.mark" if before is None else "attendance.amend",
        "attendance_record",
        entity_id=record.id,
        actor=actor,
        before=before,
        after=audit.snapshot(record),
    )
    db.session.commit()
    return record


def mark_teacher(
    actor: User,
    session: CourseSession,
    status: AttendanceStatus,
    *,
    checked_in_at: datetime | None = None,
) -> CourseSession:
    """Teacher attendance lives on the session row (PLAN.md §2.1)."""
    _assert_markable(session)
    before = audit.snapshot(session, ["teacher_status", "teacher_checked_in_at"])

    session.teacher_status = status
    session.teacher_checked_in_at = (
        (checked_in_at or utcnow()) if status in PRESENT_LIKE else None
    )
    session.teacher_recorded_by_id = actor.id
    if session.status is SessionStatus.SCHEDULED:
        session.status = SessionStatus.HELD

    db.session.flush()
    audit.record(
        "attendance.teacher_mark",
        "session",
        entity_id=session.id,
        actor=actor,
        before=before,
        after=audit.snapshot(session, ["teacher_status", "teacher_checked_in_at"]),
    )
    db.session.commit()
    return session


# ---------------------------------------------------------------- history


def history_for_student(
    student: User,
    *,
    course_ids: list[int] | None = None,
    since: date | None = None,
    until: date | None = None,
) -> list[AttendanceRecord]:
    """Per-student history, newest first — what the portal shows."""
    query = (
        select(AttendanceRecord)
        .join(CourseSession, CourseSession.id == AttendanceRecord.session_id)
        .options(
            selectinload(AttendanceRecord.session).selectinload(CourseSession.course),
        )
        .where(AttendanceRecord.student_id == student.id)
    )
    if course_ids is not None:
        query = query.where(CourseSession.course_id.in_(course_ids or [-1]))
    if since:
        query = query.where(CourseSession.session_date >= since)
    if until:
        query = query.where(CourseSession.session_date <= until)

    return list(
        db.session.scalars(
            query.order_by(CourseSession.session_date.desc(), CourseSession.start_time.desc())
        )
    )


def history_for_courses(
    course_ids: list[int],
    *,
    teacher_id: int | None = None,
    since: date | None = None,
    until: date | None = None,
) -> list[CourseSession]:
    """Per-course/teacher history — the staff and teacher direction."""
    if not course_ids:
        return []

    query = (
        select(CourseSession)
        .options(
            selectinload(CourseSession.course).selectinload(Course.course_type),
            selectinload(CourseSession.attendance).selectinload(AttendanceRecord.student),
            selectinload(CourseSession.teacher),
        )
        .where(CourseSession.course_id.in_(course_ids))
    )
    if teacher_id:
        query = query.where(CourseSession.teacher_id == teacher_id)
    if since:
        query = query.where(CourseSession.session_date >= since)
    if until:
        query = query.where(CourseSession.session_date <= until)

    return list(
        db.session.scalars(
            query.order_by(CourseSession.session_date.desc(), CourseSession.start_time.desc())
        )
    )


def summarise(records: list[AttendanceRecord]) -> dict[str, int]:
    counts = {status.value: 0 for status in AttendanceStatus}
    for record in records:
        counts[record.status.value] += 1
    return counts


def can_record(actor: User, session: CourseSession) -> bool:
    """Assistant and admin may record any session; a teacher only their own.

    Enforced in the routes through scoping, this is for hiding controls the
    user cannot use — never the only check.
    """
    if actor.role in (Role.ADMIN, Role.ASSISTANT):
        return True
    return actor.role is Role.TEACHER and session.course.teacher_id == actor.id
