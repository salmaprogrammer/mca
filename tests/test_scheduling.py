"""Sprint S2.4 — teacher scheduling conflict detection.

The bug being fixed: the prototype compares slot *start times* for equality, so
a 16:00 class and a 16:30 class pass its check while genuinely colliding. Every
test here is written against real intervals.
"""

from __future__ import annotations

from datetime import time

import pytest

from app.models.enums import Role
from app.services import courses as course_service
from app.services.scheduling import (
    ProposedSlot,
    ScheduleConflictError,
    find_conflicts,
    intervals_overlap,
)
from tests.conftest import make_course, make_user

SUNDAY = 6
WEDNESDAY = 2
MONDAY = 0


class TestIntervalOverlap:
    @pytest.mark.parametrize(
        "a_start,a_end,b_start,b_end,expected",
        [
            # The case the prototype gets wrong: different starts, real overlap.
            ((16, 0), (17, 30), (16, 30), (18, 0), True),
            # Identical.
            ((16, 0), (17, 30), (16, 0), (17, 30), True),
            # One fully inside the other.
            ((16, 0), (19, 0), (17, 0), (18, 0), True),
            # Touching end-to-start is NOT a conflict — back-to-back is normal.
            ((16, 0), (17, 30), (17, 30), (19, 0), False),
            # Clearly apart.
            ((16, 0), (17, 30), (18, 0), (19, 30), False),
            # B entirely before A.
            ((16, 0), (17, 30), (14, 0), (15, 30), False),
        ],
    )
    def test_cases(self, a_start, a_end, b_start, b_end, expected):
        assert (
            intervals_overlap(
                time(*a_start), time(*a_end), time(*b_start), time(*b_end)
            )
            is expected
        )


class TestSlotEndTime:
    def test_duration_defines_the_end(self, app):
        slot = ProposedSlot(weekday=SUNDAY, start_time=time(16, 0), duration_minutes=90)
        assert slot.end_time == time(17, 30)

    def test_past_midnight_is_clamped_not_wrapped(self, app):
        """A class running past midnight is a data error.

        Wrapping to 00:30 would make it look like an early-morning class and
        silently break every overlap comparison for that day.
        """
        slot = ProposedSlot(weekday=SUNDAY, start_time=time(23, 30), duration_minutes=90)
        assert slot.end_time == time(23, 59)


class TestTeacherConflicts:
    @pytest.fixture
    def busy_teacher(self, db, seeded_terms, seeded_course_types, admin):
        """A teacher already booked Sunday 16:00–17:30."""
        teacher = make_user(Role.TEACHER, phone="+201011119001", name="Ahmed Fathy")
        make_course(
            admin,
            teacher=teacher,
            name="GPA — Nov round",
            course_type_code="gpa_course",
            slots=[
                {"weekday": SUNDAY, "start_time": "16:00"},
                {"weekday": WEDNESDAY, "start_time": "16:00"},
            ],
        )
        return teacher

    def test_overlapping_but_not_identical_is_caught(self, app, busy_teacher):
        """The headline case. The prototype lets this through."""
        conflicts = find_conflicts(
            busy_teacher.id, [ProposedSlot(SUNDAY, time(16, 30), 90)]
        )
        assert len(conflicts) == 1

    def test_the_error_names_course_day_and_times(self, app, busy_teacher):
        """The brief asks for a named conflict, not a generic failure."""
        from flask_babel import force_locale

        with force_locale("en"):
            conflicts = find_conflicts(
                busy_teacher.id, [ProposedSlot(SUNDAY, time(16, 30), 90)]
            )
        message = conflicts[0].message
        assert "GPA — Nov round" in message
        assert "Ahmed Fathy" in message
        assert "Sunday" in message
        # Times render 12-hour with AM/PM (locale-aware AM/PM).
        assert "4:00 PM" in message and "5:30 PM" in message  # the existing slot
        assert "4:30 PM" in message and "6:00 PM" in message  # the proposed one

    def test_the_error_is_localised(self, app, busy_teacher):
        """Staff working in Arabic must get an Arabic day name, not "Sunday"."""
        from flask_babel import force_locale

        with force_locale("ar"):
            conflicts = find_conflicts(
                busy_teacher.id, [ProposedSlot(SUNDAY, time(16, 30), 90)]
            )
        assert "الأحد" in conflicts[0].message

    def test_back_to_back_is_allowed(self, app, busy_teacher):
        """17:30 start against a 16:00–17:30 class is a normal teaching day."""
        assert find_conflicts(busy_teacher.id, [ProposedSlot(SUNDAY, time(17, 30), 90)]) == []

    def test_a_different_day_is_fine(self, app, busy_teacher):
        assert find_conflicts(busy_teacher.id, [ProposedSlot(MONDAY, time(16, 0), 90)]) == []

    def test_another_teacher_is_unaffected(self, app, db, busy_teacher):
        free_teacher = make_user(Role.TEACHER, phone="+201011119002")
        assert find_conflicts(free_teacher.id, [ProposedSlot(SUNDAY, time(16, 0), 90)]) == []

    def test_a_course_does_not_conflict_with_itself_when_edited(self, app, busy_teacher):
        course = busy_teacher_course(busy_teacher)
        assert (
            find_conflicts(
                busy_teacher.id,
                [ProposedSlot(SUNDAY, time(16, 0), 90)],
                exclude_course_id=course.id,
            )
            == []
        )

    def test_archived_courses_no_longer_occupy_the_week(self, app, admin, busy_teacher):
        course = busy_teacher_course(busy_teacher)
        course_service.archive_course(admin, course)
        assert find_conflicts(busy_teacher.id, [ProposedSlot(SUNDAY, time(16, 0), 90)]) == []


def busy_teacher_course(teacher):
    """The single course created for `busy_teacher`."""
    from sqlalchemy import select

    from app.extensions import db
    from app.models.course import Course

    return db.session.scalar(select(Course).where(Course.teacher_id == teacher.id))


class TestSelfOverlap:
    def test_a_courses_own_two_slots_cannot_collide(
        self, app, db, seeded_terms, seeded_course_types, admin
    ):
        """Nothing else would catch this — the teacher has no other course yet."""
        from flask_babel import force_locale

        teacher = make_user(Role.TEACHER, phone="+201011119003")
        with force_locale("en"), pytest.raises(ScheduleConflictError) as exc:
            make_course(
                admin,
                teacher=teacher,
                course_type_code="gpa_course",
                slots=[
                    {"weekday": SUNDAY, "start_time": "16:00"},
                    {"weekday": SUNDAY, "start_time": "17:00"},
                ],
            )
        message = str(exc.value)
        assert "overlap" in message.lower()
        assert "Sunday" in message

    def test_the_self_overlap_message_is_localised(
        self, app, db, seeded_terms, seeded_course_types, admin
    ):
        """The day name must follow the locale like every other message."""
        from flask_babel import force_locale

        teacher = make_user(Role.TEACHER, phone="+201011119004")
        with force_locale("ar"), pytest.raises(ScheduleConflictError) as exc:
            make_course(
                admin,
                teacher=teacher,
                course_type_code="gpa_course",
                slots=[
                    {"weekday": SUNDAY, "start_time": "16:00"},
                    {"weekday": SUNDAY, "start_time": "17:00"},
                ],
            )
        assert "الأحد" in str(exc.value)
        assert "Sunday" not in str(exc.value)


class TestEnforcementPaths:
    """The brief requires validation on creation AND on any later edit."""

    @pytest.fixture
    def setup(self, db, seeded_terms, seeded_course_types, admin):
        first_teacher = make_user(Role.TEACHER, phone="+201011119010", name="Ahmed")
        second_teacher = make_user(Role.TEACHER, phone="+201011119011", name="Sara")
        busy = make_course(
            admin,
            teacher=first_teacher,
            name="Existing course",
            course_type_code="sat_intermediate",
            slots=[{"weekday": SUNDAY, "start_time": "16:00"}],
        )
        return admin, first_teacher, second_teacher, busy

    def test_creation_is_blocked(self, app, setup):
        admin, first_teacher, _second, _busy = setup
        with pytest.raises(ScheduleConflictError):
            make_course(
                admin,
                teacher=first_teacher,
                name="Clashing course",
                course_type_code="sat_intermediate",
                slots=[{"weekday": SUNDAY, "start_time": "16:45"}],
            )

    def test_editing_a_schedule_is_blocked(self, app, setup):
        admin, first_teacher, second_teacher, _busy = setup
        other = make_course(
            admin,
            teacher=second_teacher,
            name="Other course",
            course_type_code="sat_intermediate",
            slots=[{"weekday": MONDAY, "start_time": "16:00"}],
        )
        # Moving it onto the busy teacher's Sunday slot must fail.
        with pytest.raises(ScheduleConflictError):
            course_service.update_course(
                admin,
                other,
                name=other.name,
                course_type_id=other.course_type_id,
                teacher_id=first_teacher.id,
                slots=[{"weekday": SUNDAY, "start_time": "16:30"}],
            )

    def test_reassigning_the_teacher_alone_is_blocked(self, app, setup):
        """Same times, different teacher — the sneakiest way to double-book."""
        admin, first_teacher, second_teacher, _busy = setup
        other = make_course(
            admin,
            teacher=second_teacher,
            name="Other course",
            course_type_code="sat_intermediate",
            slots=[{"weekday": SUNDAY, "start_time": "16:00"}],
        )
        with pytest.raises(ScheduleConflictError):
            course_service.update_course(
                admin,
                other,
                name=other.name,
                course_type_id=other.course_type_id,
                teacher_id=first_teacher.id,
                slots=[{"weekday": SUNDAY, "start_time": "16:00"}],
            )

    def test_a_clean_edit_still_succeeds(self, app, setup):
        admin, _first, second_teacher, _busy = setup
        other = make_course(
            admin,
            teacher=second_teacher,
            name="Other course",
            course_type_code="sat_intermediate",
            slots=[{"weekday": MONDAY, "start_time": "16:00"}],
        )
        course_service.update_course(
            admin,
            other,
            name="Renamed",
            course_type_id=other.course_type_id,
            teacher_id=second_teacher.id,
            slots=[{"weekday": MONDAY, "start_time": "18:00"}],
        )
        assert other.name == "Renamed"
        assert other.slots[0].start_time == time(18, 0)
