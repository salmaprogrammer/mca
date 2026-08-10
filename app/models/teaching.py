"""Homework, feedback and course materials (sprints S4.1–S4.3).

Three things that look alike and are not, in one respect that matters:

* **Homework** is per *course* per date — every enrolled student sees the same
  text.
* **Feedback** is per *student* per course per date — the tightest visibility
  scope in the system. Only that student and their linked parents may read it,
  plus staff and the course's own teacher.
* **Materials** are per course, authored by the teacher, visible to everyone
  enrolled.

Dates here are plain local dates (`for_date`), not instants: "today's homework"
is a calendar concept in Africa/Cairo, not a moment in UTC.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin


class Homework(TimestampMixin, db.Model):
    __tablename__ = "homework"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        sa.ForeignKey("courses.id"), nullable=False, index=True
    )
    # Optional: homework is usually set for a date, sometimes tied to a session.
    session_id: Mapped[int | None] = mapped_column(sa.ForeignKey("sessions.id"))
    for_date: Mapped[date] = mapped_column(
        sa.Date, default=date.today, nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))

    course = relationship("Course", back_populates="homework")
    session = relationship("CourseSession")
    created_by = relationship("User", foreign_keys=[created_by_id])

    def __repr__(self) -> str:
        return f"<Homework {self.id} course={self.course_id} {self.for_date}>"


class Feedback(TimestampMixin, db.Model):
    """Private to one student and their parents.

    Nothing else in the app is scoped this tightly, so every read path goes
    through `scoping.feedback_for`, never a bare query.
    """

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        sa.ForeignKey("courses.id"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        sa.ForeignKey("users.id"), nullable=False, index=True
    )
    session_id: Mapped[int | None] = mapped_column(sa.ForeignKey("sessions.id"))
    for_date: Mapped[date] = mapped_column(
        sa.Date, default=date.today, nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))

    course = relationship("Course", back_populates="feedback")
    student = relationship("User", foreign_keys=[student_id])
    session = relationship("CourseSession")
    created_by = relationship("User", foreign_keys=[created_by_id])

    def __repr__(self) -> str:
        return f"<Feedback {self.id} student={self.student_id} {self.for_date}>"


class Material(TimestampMixin, db.Model):
    """A link the teacher shares with their enrolled students."""

    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        sa.ForeignKey("courses.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    url: Mapped[str] = mapped_column(sa.String(2048), nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))

    course = relationship("Course", back_populates="materials")
    created_by = relationship("User", foreign_keys=[created_by_id])

    def __repr__(self) -> str:
        return f"<Material {self.id} course={self.course_id} {self.title!r}>"
