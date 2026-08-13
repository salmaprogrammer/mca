"""Session generation and lifecycle (sprints S3.1, S3.2).

Open question 5, answered: the assistant sets a start date per course and the
whole round is generated at once, so staff get a forward view and "session 7 of
10" is a real number rather than a guess.
"""

from __future__ import annotations

from datetime import date, timedelta

from flask_babel import gettext as _
from sqlalchemy import select

from app.extensions import db
from app.models.course import Course, CourseSlot
from app.models.enums import SessionStatus
from app.models.session import CourseSession
from app.models.user import User
from app.services import audit


class SessionError(ValueError):
    """Something the operator needs explained."""


def first_occurrence(weekday: int, on_or_after: date) -> date:
    """The first date on/after `on_or_after` falling on `weekday` (Mon=0…Sun=6)."""
    return on_or_after + timedelta(days=(weekday - on_or_after.weekday()) % 7)


def planned_occurrences(course: Course) -> list[tuple[int, date, CourseSlot]]:
    """The full round as (sequence_no, date, slot), in chronological order.

    Walks week by week through the course's weekly slots until the type's
    total session count is reached.
    """
    if not course.start_date:
        raise SessionError(_("Set a start date on the course before generating sessions."))
    if not course.slots:
        raise SessionError(_("This course has no weekly schedule yet."))

    total = course.course_type.total_sessions
    # Order slots by when they first land, so sequence numbers run in date order.
    ordered = sorted(
        course.slots,
        key=lambda s: (first_occurrence(s.weekday, course.start_date), s.start_time),
    )

    occurrences: list[tuple[int, date, CourseSlot]] = []
    week = 0
    while len(occurrences) < total:
        for slot in ordered:
            if len(occurrences) >= total:
                break
            when = first_occurrence(slot.weekday, course.start_date) + timedelta(weeks=week)
            occurrences.append((len(occurrences) + 1, when, slot))
        week += 1
        if week > total + 52:  # paranoia: never loop forever on bad data
            raise SessionError(_("Could not lay out this course's schedule."))

    return occurrences


def generate_sessions(actor: User | None, course: Course) -> list[CourseSession]:
    """Create any missing sessions for the course. Idempotent.

    Keyed on `sequence_no`, not on date: a session that has been rescheduled
    to a different day must not be re-created at its original slot when this
    runs again.
    """
    existing = {s.sequence_no for s in course.sessions}
    created: list[CourseSession] = []

    for sequence_no, when, slot in planned_occurrences(course):
        if sequence_no in existing:
            continue
        session = CourseSession(
            course_id=course.id,
            slot_id=slot.id,
            session_date=when,
            start_time=slot.start_time,
            end_time=slot.end_time,
            sequence_no=sequence_no,
            teacher_id=course.teacher_id,
            status=SessionStatus.SCHEDULED,
        )
        db.session.add(session)
        created.append(session)

    if created:
        db.session.flush()
        audit.record(
            "session.generate",
            "course",
            entity_id=course.id,
            actor=actor,
            after={"created": len(created), "first": str(created[0].session_date)},
        )
        db.session.commit()
    return created


# ------------------------------------------------------------- lifecycle


def cancel_session(
    actor: User, session: CourseSession, *, by_teacher: bool, reason: str | None = None
) -> None:
    """Cancel a session.

    Who cancelled matters: the teacher terms make the centre liable to
    reschedule a teacher-cancelled session, while a student's absence is
    forfeited. Recording the distinction is what makes that checkable later.
    """
    if session.is_cancelled:
        raise SessionError(_("This session is already cancelled."))

    before = audit.snapshot(session, ["status", "cancellation_reason"])
    session.status = (
        SessionStatus.CANCELLED_BY_TEACHER if by_teacher else SessionStatus.CANCELLED_BY_CENTER
    )
    session.cancellation_reason = (reason or "").strip() or None

    audit.record(
        "session.cancel",
        "session",
        entity_id=session.id,
        actor=actor,
        before=before,
        after=audit.snapshot(session, ["status", "cancellation_reason"]),
    )
    db.session.commit()


def restore_session(actor: User, session: CourseSession) -> None:
    """Reverse a cancellation, putting a cancelled session back to SCHEDULED.

    Undoes a mis-click without erasing the fact it happened: the audit trail
    keeps both the cancel and the restore rows, so the timeline shows who
    cancelled, who reversed, and when.
    """
    if not session.is_cancelled:
        raise SessionError(_("This session is not cancelled, so there is nothing to reverse."))

    before = audit.snapshot(session, ["status", "cancellation_reason"])
    session.status = SessionStatus.SCHEDULED
    session.cancellation_reason = None

    audit.record(
        "session.restore",
        "session",
        entity_id=session.id,
        actor=actor,
        before=before,
        after=audit.snapshot(session, ["status", "cancellation_reason"]),
    )
    db.session.commit()


def shift_sessions_from(
    actor: User, session: CourseSession, new_date: date
) -> list[CourseSession]:
    """Move a session to a new date and slide every later scheduled session by
    the same offset. The point is fixing the first-session date after
    generation without having to reschedule each row individually.

    Only sessions with `status == SCHEDULED` move: an already-held session
    would rewrite history, and a rescheduled or cancelled one is deliberately
    parked. Returns the list of sessions that were actually shifted (the
    starting session included).
    """
    if session.is_cancelled:
        raise SessionError(
            _("A cancelled session cannot be shifted. Restore it first, then shift.")
        )
    if session.status is SessionStatus.RESCHEDULED:
        raise SessionError(
            _("This session was moved; shift its replacement instead.")
        )
    if session.status is not SessionStatus.SCHEDULED:
        raise SessionError(
            _("Only scheduled sessions can be shifted; this one has already been held.")
        )

    original_date = session.session_date
    if new_date == original_date:
        return []

    delta = new_date - original_date
    course = session.course
    to_shift = [
        s
        for s in sorted(course.sessions, key=lambda x: x.sequence_no)
        if s.sequence_no >= session.sequence_no
        and s.status is SessionStatus.SCHEDULED
    ]
    for s in to_shift:
        s.session_date = s.session_date + delta

    audit.record(
        "session.shift",
        "session",
        entity_id=session.id,
        actor=actor,
        before={
            "from_date": original_date.isoformat(),
            "sequence_no": session.sequence_no,
        },
        after={
            "to_date": new_date.isoformat(),
            "delta_days": delta.days,
            "sessions_shifted": len(to_shift),
        },
    )
    db.session.commit()
    return to_shift


def reschedule_session(
    actor: User,
    session: CourseSession,
    *,
    new_date: date,
    new_start,
    reason: str | None = None,
) -> CourseSession:
    """Move a session to another date/time, keeping the original on file.

    The original row is marked RESCHEDULED and points at the replacement, so
    the history shows that the session moved rather than silently changing
    date under an attendance record.
    """
    if session.is_cancelled:
        raise SessionError(_("A cancelled session cannot be rescheduled."))
    if session.status is SessionStatus.RESCHEDULED:
        raise SessionError(_("This session has already been moved."))

    duration = session.slot.duration_minutes if session.slot else 90
    end_minutes = new_start.hour * 60 + new_start.minute + duration
    end_minutes = min(end_minutes, 23 * 60 + 59)
    from datetime import time as _time

    replacement = CourseSession(
        course_id=session.course_id,
        slot_id=None,  # no longer sits on the weekly pattern
        session_date=new_date,
        start_time=new_start,
        end_time=_time(hour=end_minutes // 60, minute=end_minutes % 60),
        sequence_no=session.sequence_no,
        teacher_id=session.teacher_id,
        status=SessionStatus.SCHEDULED,
        notes=reason,
    )

    # Free the sequence number before inserting the replacement: it is unique
    # per course, and both rows would otherwise claim it.
    session.sequence_no = -session.sequence_no
    session.status = SessionStatus.RESCHEDULED
    db.session.add(replacement)
    db.session.flush()

    session.rescheduled_to_id = replacement.id
    audit.record(
        "session.reschedule",
        "session",
        entity_id=session.id,
        actor=actor,
        before={"date": str(session.session_date), "time": f"{session.start_time:%H:%M}"},
        after={"date": str(new_date), "time": f"{new_start:%H:%M}", "new_id": replacement.id},
    )
    db.session.commit()
    return replacement


def add_makeup_session(
    actor: User, course: Course, *, when: date, start_time, duration_minutes: int = 90
) -> CourseSession:
    """An extra session that sits on no weekly slot."""
    from datetime import time as _time

    end_minutes = min(
        start_time.hour * 60 + start_time.minute + duration_minutes, 23 * 60 + 59
    )
    highest = max((s.sequence_no for s in course.sessions), default=0)

    session = CourseSession(
        course_id=course.id,
        slot_id=None,
        session_date=when,
        start_time=start_time,
        end_time=_time(hour=end_minutes // 60, minute=end_minutes % 60),
        sequence_no=highest + 1,
        teacher_id=course.teacher_id,
        status=SessionStatus.SCHEDULED,
    )
    db.session.add(session)
    db.session.flush()
    audit.record(
        "session.makeup_added",
        "session",
        entity_id=session.id,
        actor=actor,
        after=audit.snapshot(session),
    )
    db.session.commit()
    return session


# ---------------------------------------------------------------- queries


def sessions_on(course_ids: list[int], on: date) -> list[CourseSession]:
    if not course_ids:
        return []
    return list(
        db.session.scalars(
            select(CourseSession)
            .where(
                CourseSession.course_id.in_(course_ids),
                CourseSession.session_date == on,
            )
            .order_by(CourseSession.start_time)
        )
    )


def round_progress(course: Course) -> tuple[int, int]:
    """(sessions held, total planned) — the "session 7 of 10" the brief implies."""
    held = sum(1 for s in course.sessions if s.status is SessionStatus.HELD)
    return held, course.course_type.total_sessions
