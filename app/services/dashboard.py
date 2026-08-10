"""Landing-page data for each role (sprints S7.1–S7.3).

Every dashboard answers "what needs my attention today", not "here is
everything". The queries all route through `scoping`, so a dashboard cannot
surface a row the viewer could not reach by other means.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.extensions import db
from app.models.course import Course, Enrollment
from app.models.enums import CourseStatus, MessageStatus, Role, SessionStatus
from app.models.messaging import WhatsAppMessage
from app.models.session import CourseSession
from app.models.user import User
from app.services import attendance as attendance_service
from app.services import enrollments as enrollment_service
from app.services import scoping


@dataclass
class StaffDashboard:
    on: date
    sessions_today: list = field(default_factory=list)
    unmarked_sessions: list = field(default_factory=list)
    students_without_update: list = field(default_factory=list)
    unpaid: list = field(default_factory=list)
    outstanding_total: Decimal = Decimal("0")
    unsent_messages: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    @property
    def needs_attention(self) -> bool:
        return bool(
            self.unmarked_sessions or self.unsent_messages or self.students_without_update
        )


def staff_dashboard(actor: User, on: date | None = None) -> StaffDashboard:
    on = on or date.today()
    sessions = scoping.sessions_for(actor, on=on)

    # A session is "unmarked" when it should have run and nobody recorded
    # anyone — the single most useful thing to put in front of an assistant.
    unmarked = [
        session
        for session in sessions
        if session.needs_attendance
        and len(session.attendance) < len(session.course.enrollments)
    ]

    students = scoping.students_for(actor)
    # Prepared is not sent. A student whose update was composed and left
    # sitting has heard nothing, so they still count as waiting.
    messaged_today = {
        row
        for row in db.session.scalars(
            select(WhatsAppMessage.student_id).where(
                WhatsAppMessage.batch_date == on,
                WhatsAppMessage.status == MessageStatus.SENT,
            )
        )
    }
    without_update = [s for s in students if s.id not in messaged_today]

    unpaid = enrollment_service.unpaid_for(actor)

    counts = {}
    if actor.role is Role.ADMIN:
        counts = dict(
            db.session.execute(
                select(User.role, func.count(User.id)).group_by(User.role)
            ).all()
        )
    counts["courses"] = db.session.scalar(
        select(func.count(Course.id)).where(Course.status != CourseStatus.ARCHIVED)
    )

    from app.services import whatsapp as whatsapp_service

    return StaffDashboard(
        on=on,
        sessions_today=sessions,
        unmarked_sessions=unmarked,
        students_without_update=without_update,
        unpaid=unpaid,
        outstanding_total=enrollment_service.outstanding_total(unpaid),
        unsent_messages=whatsapp_service.waiting_to_be_sent(actor, limit=5),
        counts=counts,
    )


@dataclass
class TeacherDashboard:
    on: date
    sessions_today: list = field(default_factory=list)
    next_session: CourseSession | None = None
    courses: list = field(default_factory=list)
    students: list = field(default_factory=list)
    week: list = field(default_factory=list)


def teacher_dashboard(actor: User, on: date | None = None) -> TeacherDashboard:
    from app.models.course import WEEK_ORDER

    on = on or date.today()
    courses = scoping.courses_for(actor)

    by_day: dict[int, list] = {day: [] for day in WEEK_ORDER}
    for course in courses:
        for slot in course.slots:
            by_day[slot.weekday].append((slot, course))
    for day in by_day:
        by_day[day].sort(key=lambda pair: pair[0].start_time)

    return TeacherDashboard(
        on=on,
        sessions_today=scoping.sessions_for(actor, on=on),
        next_session=next_session_for(actor, after=on),
        courses=courses,
        students=scoping.students_for(actor),
        week=[(day, by_day[day]) for day in WEEK_ORDER],
    )


@dataclass
class PortalDashboard:
    student: User | None = None
    courses: list = field(default_factory=list)
    next_session: CourseSession | None = None
    attendance: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    homework: list = field(default_factory=list)
    feedback: list = field(default_factory=list)
    unpaid: list = field(default_factory=list)


def portal_dashboard(actor: User, student: User | None) -> PortalDashboard:
    if student is None:
        return PortalDashboard()

    courses = scoping.courses_for_student(actor, student)
    course_ids = [c.id for c in courses]
    records = attendance_service.history_for_student(student, course_ids=course_ids)

    unpaid = [
        enrollment
        for course in courses
        if (enrollment := scoping.enrollment_for(course, student))
        and enrollment.payment_status.value == "unpaid"
    ]

    return PortalDashboard(
        student=student,
        courses=courses,
        next_session=_next_session_in(course_ids),
        attendance=records[:6],
        summary=attendance_service.summarise(records),
        homework=scoping.homework_for(actor, limit=5),
        feedback=scoping.feedback_for(actor, student=student, limit=5),
        unpaid=unpaid,
    )


# ---------------------------------------------------------------- helpers


def next_session_for(actor: User, after: date | None = None) -> CourseSession | None:
    course_ids = [c.id for c in scoping.courses_for(actor)]
    return _next_session_in(course_ids, after)


def _next_session_in(course_ids: list[int], after: date | None = None) -> CourseSession | None:
    """The soonest session still to come, ignoring cancelled and moved ones."""
    if not course_ids:
        return None
    return db.session.scalar(
        select(CourseSession)
        .where(
            CourseSession.course_id.in_(course_ids),
            CourseSession.session_date >= (after or date.today()),
            CourseSession.status == SessionStatus.SCHEDULED,
        )
        .order_by(CourseSession.session_date, CourseSession.start_time)
        .limit(1)
    )


def enrolled_student_count() -> int:
    return db.session.scalar(
        select(func.count(func.distinct(Enrollment.student_id)))
    ) or 0
