"""Data integrity audit (sprint S11.3).

Constraints catch most of this at write time. These checks exist for what a
constraint cannot express, and for damage that predates a constraint — a
restore from an old backup, a hand-edited row, a migration that ran halfway.

Run with `flask integrity-check`. Read-only: it reports, it never repairs,
because the right repair for an orphaned record is a judgement call about a
real family's data.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Finding:
    check: str
    count: int
    detail: str
    examples: list = field(default_factory=list)


def run_all() -> list[Finding]:
    findings: list[Finding] = []
    findings += _orphan_checks()
    findings += _consistency_checks()
    findings += _timezone_checks()
    return findings


def _orphan_checks() -> list[Finding]:
    from sqlalchemy import select

    from app.extensions import db
    from app.models.course import Course, CourseSlot, Enrollment
    from app.models.enums import Role
    from app.models.session import AttendanceRecord, CourseSession
    from app.models.teaching import Feedback, Homework, Material
    from app.models.user import ParentLink, User

    out = []

    def orphans(label, stmt, detail):
        rows = list(db.session.scalars(stmt))
        if rows:
            out.append(Finding(label, len(rows), detail, [r.id for r in rows[:5]]))

    course_ids = select(Course.id)
    user_ids = select(User.id)
    session_ids = select(CourseSession.id)

    orphans(
        "enrolments without a course",
        select(Enrollment).where(Enrollment.course_id.not_in(course_ids)),
        "the enrolment points at a course row that no longer exists",
    )
    orphans(
        "enrolments without a student",
        select(Enrollment).where(Enrollment.student_id.not_in(user_ids)),
        "the enrolled user was deleted",
    )
    orphans(
        "attendance without a session",
        select(AttendanceRecord).where(AttendanceRecord.session_id.not_in(session_ids)),
        "attendance recorded against a session that no longer exists",
    )
    orphans(
        "sessions without a course",
        select(CourseSession).where(CourseSession.course_id.not_in(course_ids)),
        "",
    )
    orphans(
        "slots without a course",
        select(CourseSlot).where(CourseSlot.course_id.not_in(course_ids)),
        "",
    )
    for model, label in (
        (Homework, "homework"),
        (Feedback, "feedback"),
        (Material, "materials"),
    ):
        orphans(
            f"{label} without a course",
            select(model).where(model.course_id.not_in(course_ids)),
            "",
        )
    orphans(
        "parent links to a missing user",
        select(ParentLink).where(
            ParentLink.parent_id.not_in(user_ids) | ParentLink.student_id.not_in(user_ids)
        ),
        "one side of the family link was deleted",
    )

    # A parent link must actually join a parent to a student.
    wrong_roles = list(
        db.session.scalars(
            select(ParentLink)
            .join(User, User.id == ParentLink.student_id)
            .where(User.role != Role.STUDENT)
        )
    )
    if wrong_roles:
        out.append(
            Finding(
                "parent links pointing at a non-student",
                len(wrong_roles),
                "the 'student' side of the link is not a student account",
                [r.id for r in wrong_roles[:5]],
            )
        )

    return out


def _consistency_checks() -> list[Finding]:
    from sqlalchemy import func, select

    from app.extensions import db
    from app.models.course import Course, Enrollment
    from app.models.enums import AttendanceStatus, PaymentStatus, Role
    from app.models.session import AttendanceRecord, CourseSession
    from app.models.user import User

    out = []

    # A student marked present at a course they are not enrolled in.
    stray = list(
        db.session.scalars(
            select(AttendanceRecord)
            .join(CourseSession, CourseSession.id == AttendanceRecord.session_id)
            .outerjoin(
                Enrollment,
                (Enrollment.course_id == CourseSession.course_id)
                & (Enrollment.student_id == AttendanceRecord.student_id),
            )
            .where(Enrollment.id.is_(None))
        )
    )
    if stray:
        out.append(
            Finding(
                "attendance for a non-enrolled student",
                len(stray),
                "usually means the student was unenrolled after being marked",
                [r.id for r in stray[:5]],
            )
        )

    # Paid but with no payment timestamp, or unpaid but carrying one.
    inconsistent = list(
        db.session.scalars(
            select(Enrollment).where(
                (
                    (Enrollment.payment_status == PaymentStatus.PAID)
                    & Enrollment.paid_at.is_(None)
                )
                | (
                    (Enrollment.payment_status == PaymentStatus.UNPAID)
                    & Enrollment.paid_at.is_not(None)
                )
            )
        )
    )
    if inconsistent:
        out.append(
            Finding(
                "payment status and paid_at disagree",
                len(inconsistent),
                "an unpaid row carrying a payment date will mislead any report",
                [r.id for r in inconsistent[:5]],
            )
        )

    # Absent students should carry no check-in time.
    absent_with_time = db.session.scalar(
        select(func.count(AttendanceRecord.id)).where(
            AttendanceRecord.status.in_([AttendanceStatus.ABSENT, AttendanceStatus.EXCUSED]),
            AttendanceRecord.checked_in_at.is_not(None),
        )
    )
    if absent_with_time:
        out.append(
            Finding(
                "absent students with a check-in time",
                absent_with_time,
                "someone marked absent cannot have arrived",
            )
        )

    # A course whose teacher is not a teacher.
    bad_teacher = list(
        db.session.scalars(
            select(Course).join(User, User.id == Course.teacher_id).where(User.role != Role.TEACHER)
        )
    )
    if bad_teacher:
        out.append(
            Finding(
                "courses assigned to a non-teacher",
                len(bad_teacher),
                "",
                [c.id for c in bad_teacher[:5]],
            )
        )

    # Students with no way to be reached — no login and no linked parent.
    unreachable = list(
        db.session.scalars(
            select(User).where(
                User.role == Role.STUDENT,
                User.phone.is_(None),
                ~User.parent_links.any(),
            )
        )
    )
    if unreachable:
        out.append(
            Finding(
                "students with no contact route",
                len(unreachable),
                "no phone of their own and no linked parent — they get no updates",
                [u.id for u in unreachable[:5]],
            )
        )

    return out


def _timezone_checks() -> list[Finding]:
    """Spot-check the two timezone rules (PLAN.md §2.3)."""
    from sqlalchemy import select

    from app.extensions import db
    from app.models.course import CourseSlot
    from app.models.session import AttendanceRecord, CourseSession

    out = []

    naive = [
        record
        for record in db.session.scalars(
            select(AttendanceRecord).where(AttendanceRecord.checked_in_at.is_not(None)).limit(200)
        )
        if record.checked_in_at.tzinfo is None
    ]
    if naive:
        out.append(
            Finding(
                "check-in timestamps without a timezone",
                len(naive),
                "UtcDateTime should re-tag these; a naive value will display wrong",
                [r.id for r in naive[:5]],
            )
        )

    # A generated session must keep the wall-clock time of its slot. If these
    # ever drift, a DST boundary was applied to a value that should not move.
    drifted = list(
        db.session.scalars(
            select(CourseSession)
            .join(CourseSlot, CourseSlot.id == CourseSession.slot_id)
            .where(CourseSession.start_time != CourseSlot.start_time)
        )
    )
    if drifted:
        out.append(
            Finding(
                "sessions whose time drifted from their slot",
                len(drifted),
                "wall-clock session times must not shift across a DST boundary",
                [s.id for s in drifted[:5]],
            )
        )

    backwards = list(
        db.session.scalars(
            select(CourseSession).where(CourseSession.end_time <= CourseSession.start_time)
        )
    )
    if backwards:
        out.append(
            Finding(
                "sessions ending before they start",
                len(backwards),
                "",
                [s.id for s in backwards[:5]],
            )
        )

    return out
