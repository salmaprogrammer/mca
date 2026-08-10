"""Dated session instances and attendance records (sprints S3.1–S3.4).

The deliberate departure from the brief (PLAN.md §2.1): a weekly `CourseSlot`
is a recurring *pattern*, not something anyone can attend. `CourseSession` is
the concrete dated occurrence, and it is what attendance, cancellation,
rescheduling and round progress all hang off.

Teacher attendance lives on the session row rather than in a parallel
attendance table: there is exactly one teacher per session, so a second row
would be redundant and would make "did this session actually run" awkward.

Timezone (PLAN.md §2.3): `session_date` / `start_time` / `end_time` are naive
local wall clock — "Sunday 16:00" must stay 16:00 across a DST boundary.
`checked_in_at` and `recorded_at` are timezone-aware UTC.
"""

from __future__ import annotations

from datetime import date, datetime, time

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UtcDateTime, utcnow
from app.models.enums import AttendanceStatus, SessionStatus, enum_column

CANCELLED_STATUSES = (
    SessionStatus.CANCELLED_BY_TEACHER,
    SessionStatus.CANCELLED_BY_CENTER,
)


class CourseSession(TimestampMixin, db.Model):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        sa.ForeignKey("courses.id"), nullable=False, index=True
    )
    # NULL means an ad-hoc session — a make-up class that sits on no weekly slot.
    slot_id: Mapped[int | None] = mapped_column(sa.ForeignKey("course_slots.id"))

    session_date: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    start_time: Mapped[time] = mapped_column(sa.Time, nullable=False)
    end_time: Mapped[time] = mapped_column(sa.Time, nullable=False)
    sequence_no: Mapped[int] = mapped_column(nullable=False)

    status: Mapped[SessionStatus] = mapped_column(
        enum_column(SessionStatus, 24),
        default=SessionStatus.SCHEDULED,
        nullable=False,
        index=True,
    )
    cancellation_reason: Mapped[str | None] = mapped_column(sa.String(255))
    # Set on the original when it is moved; points at the replacement session.
    rescheduled_to_id: Mapped[int | None] = mapped_column(sa.ForeignKey("sessions.id"))

    # Snapshot of who was due to teach it — a later course reassignment must not
    # silently rewrite who was recorded absent last month.
    teacher_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"), index=True)
    teacher_status: Mapped[AttendanceStatus | None] = mapped_column(
        enum_column(AttendanceStatus, 12)
    )
    teacher_checked_in_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    teacher_recorded_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))

    notes: Mapped[str | None] = mapped_column(sa.Text)

    course = relationship("Course", back_populates="sessions")
    slot = relationship("CourseSlot")
    teacher = relationship("User", foreign_keys=[teacher_id])
    rescheduled_to = relationship(
        "CourseSession", remote_side=[id], foreign_keys=[rescheduled_to_id]
    )
    attendance = relationship(
        "AttendanceRecord",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        sa.UniqueConstraint("course_id", "sequence_no", name="uq_session_sequence"),
    )

    @property
    def is_cancelled(self) -> bool:
        return self.status in CANCELLED_STATUSES

    @property
    def needs_attendance(self) -> bool:
        """Cancelled and rescheduled sessions are not waiting to be marked."""
        return self.status in (SessionStatus.SCHEDULED, SessionStatus.HELD)

    @property
    def starts_at(self) -> datetime:
        """Naive local datetime, for sorting and comparison only."""
        return datetime.combine(self.session_date, self.start_time)

    def attendance_for(self, student_id: int) -> AttendanceRecord | None:
        for record in self.attendance:
            if record.student_id == student_id:
                return record
        return None

    def label(self) -> str:
        return f"{self.session_date:%Y-%m-%d} {self.start_time:%H:%M}–{self.end_time:%H:%M}"

    def __repr__(self) -> str:
        return f"<CourseSession {self.id} course={self.course_id} #{self.sequence_no}>"


class AttendanceRecord(db.Model):
    """One student's attendance at one session.

    Unique per (session, student): re-marking updates the existing row and
    writes an audit entry rather than stacking duplicates, so an attendance
    dispute has one authoritative record plus its change history.
    """

    __tablename__ = "attendance_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        sa.ForeignKey("sessions.id"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        sa.ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[AttendanceStatus] = mapped_column(
        enum_column(AttendanceStatus, 12), nullable=False
    )
    # The brief insists attendance carries a time of check-in, not just a flag.
    checked_in_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    recorded_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))
    recorded_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, nullable=False
    )

    session = relationship("CourseSession", back_populates="attendance")
    student = relationship("User", foreign_keys=[student_id])
    recorded_by = relationship("User", foreign_keys=[recorded_by_id])

    __table_args__ = (
        sa.UniqueConstraint("session_id", "student_id", name="uq_attendance_once"),
    )

    @property
    def was_present(self) -> bool:
        return self.status in (AttendanceStatus.PRESENT, AttendanceStatus.LATE)

    def __repr__(self) -> str:
        return f"<AttendanceRecord session={self.session_id} student={self.student_id}>"
