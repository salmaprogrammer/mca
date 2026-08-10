"""Course catalogue: fixed types, course instances, weekly slots, enrolments.

Sprints S2.1–S2.3, plus the enrolment join table (see the note in S2.7 of
PLAN.md §6 — courses are meaningless to a student without it, and P3 attendance
depends on it).

Weekday convention: `CourseSlot.weekday` stores **Python's** numbering,
Monday=0 … Sunday=6, so `date.weekday()` can be compared directly when P3
generates dated sessions. The UI renders the week Saturday-first for Egypt;
that is a display concern only, handled by `WEEK_ORDER` below.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UtcDateTime
from app.models.enums import (
    BookingStatus,
    CourseCycle,
    CourseStatus,
    PaymentStatus,
    enum_column,
)

# Monday=0 … Sunday=6, listed in the order Egyptian weeks are read.
WEEK_ORDER = [5, 6, 0, 1, 2, 3, 4]

WEEKDAY_LABELS = {
    0: ("Monday", "الاثنين"),
    1: ("Tuesday", "الثلاثاء"),
    2: ("Wednesday", "الأربعاء"),
    3: ("Thursday", "الخميس"),
    4: ("Friday", "الجمعة"),
    5: ("Saturday", "السبت"),
    6: ("Sunday", "الأحد"),
}


def weekday_label(weekday: int, locale: str = "en") -> str:
    en, ar = WEEKDAY_LABELS[weekday]
    return ar if locale == "ar" else en


class CourseType(db.Model):
    """The six fixed products from the brief.

    Deliberately has no create/edit/delete route anywhere in the app — that
    absence is how "types 1–6 are not freely editable" is enforced.

    Per open question 3, the *type* is what students see. `has_own_subject`
    marks the one type whose label is a pricing package rather than a subject
    ("1 session/week — 4 sessions/month"); those courses display their own
    name instead.
    """

    __tablename__ = "course_types"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    code: Mapped[str] = mapped_column(sa.String(40), unique=True, nullable=False)
    label_en: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    label_ar: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    sessions_per_week: Mapped[int] = mapped_column(nullable=False)
    cycle: Mapped[CourseCycle] = mapped_column(enum_column(CourseCycle, 10), nullable=False)
    total_sessions: Mapped[int] = mapped_column(nullable=False)
    has_own_subject: Mapped[bool] = mapped_column(default=True, nullable=False)

    courses = relationship("Course", back_populates="course_type")

    def label(self, locale: str = "en") -> str:
        return self.label_ar if locale == "ar" else self.label_en

    def __repr__(self) -> str:
        return f"<CourseType {self.id} {self.code}>"


class Course(TimestampMixin, db.Model):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    course_type_id: Mapped[int] = mapped_column(
        sa.ForeignKey("course_types.id"), nullable=False, index=True
    )
    teacher_id: Mapped[int] = mapped_column(
        sa.ForeignKey("users.id"), nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(sa.Text)
    cover_image_path: Mapped[str | None] = mapped_column(sa.String(255))
    price_egp: Mapped[Decimal] = mapped_column(sa.Numeric(10, 2), default=0, nullable=False)
    trial_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    status: Mapped[CourseStatus] = mapped_column(
        enum_column(CourseStatus, 16), default=CourseStatus.ACTIVE, nullable=False, index=True
    )
    # Anchors session generation in P3 (open question 5: the assistant sets it).
    start_date: Mapped[date | None] = mapped_column(sa.Date)
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))

    course_type = relationship("CourseType", back_populates="courses")
    teacher = relationship("User", foreign_keys=[teacher_id])
    slots = relationship(
        "CourseSlot",
        back_populates="course",
        cascade="all, delete-orphan",
        order_by="CourseSlot.start_time",
    )
    enrollments = relationship(
        "Enrollment", back_populates="course", cascade="all, delete-orphan"
    )
    sessions = relationship(
        "CourseSession",
        back_populates="course",
        cascade="all, delete-orphan",
        order_by="CourseSession.sequence_no",
    )
    homework = relationship(
        "Homework",
        back_populates="course",
        cascade="all, delete-orphan",
        order_by="Homework.for_date.desc()",
    )
    feedback = relationship(
        "Feedback",
        back_populates="course",
        cascade="all, delete-orphan",
        order_by="Feedback.for_date.desc()",
    )
    materials = relationship(
        "Material",
        back_populates="course",
        cascade="all, delete-orphan",
        order_by="Material.created_at.desc()",
    )

    # --- display -----------------------------------------------------

    def display_title(self, locale: str = "en") -> str:
        """What a student sees.

        Open question 3: the type is the product, so it leads. The one type
        without a subject in its label falls back to the course's own name.
        """
        if not self.course_type or not self.course_type.has_own_subject:
            return self.name
        return self.course_type.label(locale)

    @property
    def students(self) -> list:
        return [e.student for e in self.enrollments]

    def slots_ordered_for_display(self) -> list[CourseSlot]:
        return sorted(self.slots, key=lambda s: (WEEK_ORDER.index(s.weekday), s.start_time))

    def __repr__(self) -> str:
        return f"<Course {self.id} {self.name!r}>"


class CourseSlot(db.Model):
    """One recurring weekly meeting: a weekday, a start time, a length.

    `duration_minutes` is what makes the brief's "overlapping time" rule
    actually checkable — without a length you can only compare exact start
    times, which is the bug in the prototype (PLAN.md §3).
    """

    __tablename__ = "course_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        sa.ForeignKey("courses.id"), nullable=False, index=True
    )
    weekday: Mapped[int] = mapped_column(nullable=False)  # Python: Mon=0 … Sun=6
    start_time: Mapped[time] = mapped_column(sa.Time, nullable=False)  # naive local wall clock
    duration_minutes: Mapped[int] = mapped_column(default=90, nullable=False)

    course = relationship("Course", back_populates="slots")

    __table_args__ = (
        sa.CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_course_slots_weekday"),
        sa.CheckConstraint("duration_minutes > 0", name="ck_course_slots_duration"),
    )

    @property
    def end_time(self) -> time:
        start = timedelta(hours=self.start_time.hour, minutes=self.start_time.minute)
        end = start + timedelta(minutes=self.duration_minutes)
        # Clamp rather than wrap: a class running past midnight is a data error,
        # and silently wrapping would make the overlap test wrong.
        total = min(int(end.total_seconds() // 60), 23 * 60 + 59)
        return time(hour=total // 60, minute=total % 60)

    def label(self, locale: str = "en") -> str:
        return (
            f"{weekday_label(self.weekday, locale)} "
            f"{self.start_time:%H:%M}–{self.end_time:%H:%M}"
        )

    def __repr__(self) -> str:
        return f"<CourseSlot {self.weekday} {self.start_time:%H:%M}>"


class Enrollment(TimestampMixin, db.Model):
    """Which student is in which course, carrying booking and payment state.

    Created in P2 because a course no student can join is invisible to the
    portal and unusable by P3 attendance. The booking/payment *editing* UI and
    the portal's read-only display remain P5 (sprints S5.2, S5.4).
    """

    __tablename__ = "enrollments"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        sa.ForeignKey("courses.id"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        sa.ForeignKey("users.id"), nullable=False, index=True
    )
    booking_status: Mapped[BookingStatus] = mapped_column(
        enum_column(BookingStatus, 16), default=BookingStatus.BOOKED, nullable=False
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        enum_column(PaymentStatus, 10), default=PaymentStatus.UNPAID, nullable=False
    )
    # Snapshot of the price at enrolment: the course price may change later and
    # must not silently rewrite what this family was asked to pay.
    amount_due: Mapped[Decimal] = mapped_column(sa.Numeric(10, 2), default=0, nullable=False)
    # The audit log records every status change, but a plain timestamp here is
    # what a "who is overdue" report needs without walking the audit trail.
    paid_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))

    course = relationship("Course", back_populates="enrollments")
    student = relationship("User", foreign_keys=[student_id])

    __table_args__ = (
        sa.UniqueConstraint("course_id", "student_id", name="uq_enrollment_once"),
    )

    def __repr__(self) -> str:
        return f"<Enrollment course={self.course_id} student={self.student_id}>"
