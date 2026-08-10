"""Teacher scheduling conflict detection (sprint S2.4).

The brief: a teacher cannot hold two slots on the same day at **overlapping**
times, validated on creation *and* on any later edit, returning an error that
names the colliding course, day and time.

The prototype compares start times for equality, so 16:00 and 16:30 pass its
check while genuinely colliding. A slot here is a real interval — start plus
duration — and overlap is the standard half-open test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time, timedelta

from flask_babel import gettext as _
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.course import Course, CourseSlot, weekday_label
from app.models.enums import CourseStatus


class ScheduleConflictError(ValueError):
    """Raised with a message naming every collision found."""

    def __init__(self, conflicts: list[Conflict]):
        self.conflicts = conflicts
        super().__init__(" ".join(c.message for c in conflicts))


@dataclass(frozen=True)
class ProposedSlot:
    """A slot being considered, before it exists in the database."""

    weekday: int
    start_time: time
    duration_minutes: int = 90

    @property
    def end_time(self) -> time:
        start = timedelta(hours=self.start_time.hour, minutes=self.start_time.minute)
        total = int((start + timedelta(minutes=self.duration_minutes)).total_seconds() // 60)
        total = min(total, 23 * 60 + 59)
        return time(hour=total // 60, minute=total % 60)


@dataclass(frozen=True)
class Conflict:
    course_name: str
    weekday: int
    existing_start: time
    existing_end: time
    proposed_start: time
    proposed_end: time
    message: str


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def intervals_overlap(
    start_a: time, end_a: time, start_b: time, end_b: time
) -> bool:
    """Half-open overlap: touching end-to-start is fine, anything else is not.

    A class ending at 17:30 and the next starting at 17:30 do not collide.
    """
    return _minutes(start_a) < _minutes(end_b) and _minutes(start_b) < _minutes(end_a)


def find_conflicts(
    teacher_id: int,
    proposed: list[ProposedSlot],
    *,
    exclude_course_id: int | None = None,
) -> list[Conflict]:
    """Every collision between `proposed` and the teacher's existing schedule.

    Archived courses are ignored — they no longer occupy the teacher's week.
    `exclude_course_id` lets a course be edited without colliding with itself.
    """
    if not teacher_id or not proposed:
        return []

    query = (
        select(Course)
        .options(selectinload(Course.slots))
        .where(
            Course.teacher_id == teacher_id,
            Course.status != CourseStatus.ARCHIVED,
        )
    )
    if exclude_course_id is not None:
        query = query.where(Course.id != exclude_course_id)

    conflicts: list[Conflict] = []
    for course in db.session.scalars(query):
        for existing in course.slots:
            for candidate in proposed:
                if existing.weekday != candidate.weekday:
                    continue
                if not intervals_overlap(
                    existing.start_time,
                    existing.end_time,
                    candidate.start_time,
                    candidate.end_time,
                ):
                    continue
                conflicts.append(
                    _build_conflict(course, existing, candidate)
                )
    return conflicts


def _build_conflict(course: Course, existing: CourseSlot, candidate: ProposedSlot) -> Conflict:
    from flask_babel import get_locale

    locale = str(get_locale() or "en")
    teacher_name = course.teacher.full_name if course.teacher else _("This teacher")
    message = _(
        "%(teacher)s already teaches «%(course)s» on %(day)s "
        "%(existing_start)s–%(existing_end)s, which overlaps "
        "%(new_start)s–%(new_end)s.",
        teacher=teacher_name,
        course=course.name,
        day=weekday_label(existing.weekday, locale),
        existing_start=f"{existing.start_time:%H:%M}",
        existing_end=f"{existing.end_time:%H:%M}",
        new_start=f"{candidate.start_time:%H:%M}",
        new_end=f"{candidate.end_time:%H:%M}",
    )
    return Conflict(
        course_name=course.name,
        weekday=existing.weekday,
        existing_start=existing.start_time,
        existing_end=existing.end_time,
        proposed_start=candidate.start_time,
        proposed_end=candidate.end_time,
        message=message,
    )


def find_self_overlaps(proposed: list[ProposedSlot]) -> list[str]:
    """Two slots of the *same* course that collide with each other.

    Easy to do by accident when a 2/week course gets both slots on the same
    day, and nothing else would catch it.
    """
    from flask_babel import get_locale

    locale = str(get_locale() or "en")
    messages = []
    for i, first in enumerate(proposed):
        for second in proposed[i + 1 :]:
            if first.weekday != second.weekday:
                continue
            if intervals_overlap(
                first.start_time, first.end_time, second.start_time, second.end_time
            ):
                messages.append(
                    _(
                        "This course has two sessions that overlap on %(day)s: "
                        "%(a_start)s–%(a_end)s and %(b_start)s–%(b_end)s.",
                        day=weekday_label(first.weekday, locale),
                        a_start=f"{first.start_time:%H:%M}",
                        a_end=f"{first.end_time:%H:%M}",
                        b_start=f"{second.start_time:%H:%M}",
                        b_end=f"{second.end_time:%H:%M}",
                    )
                )
    return messages


def assert_no_conflicts(
    teacher_id: int,
    proposed: list[ProposedSlot],
    *,
    exclude_course_id: int | None = None,
) -> None:
    """Raise if this teacher cannot take these slots.

    Called from every path that can change a teacher's schedule: course
    creation, course edit, teacher reassignment, and slot edit.
    """
    self_overlaps = find_self_overlaps(proposed)
    if self_overlaps:
        raise ScheduleConflictError(
            [
                Conflict("", 0, time(0, 0), time(0, 0), time(0, 0), time(0, 0), message)
                for message in self_overlaps
            ]
        )

    conflicts = find_conflicts(teacher_id, proposed, exclude_course_id=exclude_course_id)
    if conflicts:
        raise ScheduleConflictError(conflicts)
