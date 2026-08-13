"""Sprints S3.1 and S3.2 — session generation and lifecycle."""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from app.models.enums import Role, SessionStatus
from app.services import sessions as session_service
from tests.conftest import make_course, make_user

SUNDAY = 6
WEDNESDAY = 2
MONDAY = 0

# 2026-09-06 is a Sunday.
START = date(2026, 9, 6)


@pytest.fixture
def gpa_course(db, seeded_terms, seeded_course_types, admin):
    """GPA Course: 2 sessions/week, 8 per month, Sunday + Wednesday 16:00."""
    teacher = make_user(Role.TEACHER, phone="+201011117001", name="Ahmed Fathy")
    return make_course(
        admin,
        teacher=teacher,
        name="GPA — Nov round",
        course_type_code="gpa_course",
        slots=[
            {"weekday": SUNDAY, "start_time": "16:00"},
            {"weekday": WEDNESDAY, "start_time": "16:00"},
        ],
        start_date=START,
    )


class TestFirstOccurrence:
    def test_start_date_itself_counts(self):
        assert session_service.first_occurrence(SUNDAY, START) == START

    def test_walks_forward_to_the_next_matching_day(self):
        # Wednesday after Sunday 6 Sept is 9 Sept.
        assert session_service.first_occurrence(WEDNESDAY, START) == date(2026, 9, 9)

    def test_never_walks_backwards(self):
        result = session_service.first_occurrence(MONDAY, START)
        assert result >= START


class TestGeneration:
    def test_creates_exactly_the_types_total(self, app, gpa_course):
        created = session_service.generate_sessions(None, gpa_course)
        assert len(created) == 8
        assert len(gpa_course.sessions) == 8

    def test_sessions_land_on_the_right_weekdays(self, app, gpa_course):
        session_service.generate_sessions(None, gpa_course)
        weekdays = {s.session_date.weekday() for s in gpa_course.sessions}
        assert weekdays == {SUNDAY, WEDNESDAY}

    def test_sequence_numbers_run_in_date_order(self, app, gpa_course):
        session_service.generate_sessions(None, gpa_course)
        ordered = sorted(gpa_course.sessions, key=lambda s: s.sequence_no)
        dates = [s.session_date for s in ordered]
        assert dates == sorted(dates)
        assert [s.sequence_no for s in ordered] == list(range(1, 9))

    def test_end_time_comes_from_the_slot_duration(self, app, gpa_course):
        session_service.generate_sessions(None, gpa_course)
        first = gpa_course.sessions[0]
        assert first.start_time == time(16, 0)
        assert first.end_time == time(17, 30)

    def test_the_round_spans_four_weeks(self, app, gpa_course):
        """8 sessions at 2/week — the last one should be four weeks out."""
        session_service.generate_sessions(None, gpa_course)
        last = max(s.session_date for s in gpa_course.sessions)
        assert last - START == timedelta(days=24)  # Wed of week 4

    def test_running_twice_creates_nothing_extra(self, app, gpa_course):
        session_service.generate_sessions(None, gpa_course)
        again = session_service.generate_sessions(None, gpa_course)
        assert again == []
        assert len(gpa_course.sessions) == 8

    def test_a_missing_session_is_topped_up(self, app, db, gpa_course):
        session_service.generate_sessions(None, gpa_course)
        db.session.delete(gpa_course.sessions[3])
        db.session.commit()
        db.session.refresh(gpa_course)

        created = session_service.generate_sessions(None, gpa_course)
        assert len(created) == 1
        assert len(gpa_course.sessions) == 8

    def test_no_start_date_is_refused_with_a_reason(
        self, app, db, seeded_terms, seeded_course_types, admin
    ):
        teacher = make_user(Role.TEACHER, phone="+201011117002")
        course = make_course(admin, teacher=teacher, course_type_code="gpa_course")
        assert course.start_date is None
        with pytest.raises(session_service.SessionError):
            session_service.generate_sessions(None, course)

    def test_a_one_per_week_type_spreads_over_its_weeks(
        self, app, db, seeded_terms, seeded_course_types, admin
    ):
        teacher = make_user(Role.TEACHER, phone="+201011117003")
        course = make_course(
            admin,
            teacher=teacher,
            course_type_code="sat_intermediate",  # 1/week, 5/month
            slots=[{"weekday": SUNDAY, "start_time": "16:00"}],
            start_date=START,
        )
        session_service.generate_sessions(None, course)
        dates = sorted(s.session_date for s in course.sessions)
        assert len(dates) == 5
        assert dates[-1] - dates[0] == timedelta(days=28)

    def test_advanced_course_lays_out_fourteen_over_seven_weeks(
        self, app, db, seeded_terms, seeded_course_types, admin
    ):
        """Open question 2, answered: 2/week × 7 weeks."""
        teacher = make_user(Role.TEACHER, phone="+201011117004")
        course = make_course(
            admin,
            teacher=teacher,
            course_type_code="advanced",
            slots=[
                {"weekday": SUNDAY, "start_time": "10:00"},
                {"weekday": WEDNESDAY, "start_time": "10:00"},
            ],
            start_date=START,
        )
        session_service.generate_sessions(None, course)
        assert len(course.sessions) == 14
        span = max(s.session_date for s in course.sessions) - START
        assert span == timedelta(days=45)  # Wed of week 7


class TestCancellation:
    def test_cancelling_records_who_cancelled(self, app, admin, gpa_course):
        """The teacher terms treat the two cases very differently."""
        session_service.generate_sessions(None, gpa_course)
        session = gpa_course.sessions[0]

        session_service.cancel_session(admin, session, by_teacher=True, reason="Ill")
        assert session.status is SessionStatus.CANCELLED_BY_TEACHER
        assert session.cancellation_reason == "Ill"

    def test_centre_cancellation_is_a_distinct_status(self, app, admin, gpa_course):
        session_service.generate_sessions(None, gpa_course)
        session = gpa_course.sessions[1]
        session_service.cancel_session(admin, session, by_teacher=False)
        assert session.status is SessionStatus.CANCELLED_BY_CENTER

    def test_a_cancelled_session_no_longer_wants_attendance(self, app, admin, gpa_course):
        session_service.generate_sessions(None, gpa_course)
        session = gpa_course.sessions[0]
        session_service.cancel_session(admin, session, by_teacher=True)
        assert session.needs_attendance is False

    def test_cancelling_twice_is_refused(self, app, admin, gpa_course):
        session_service.generate_sessions(None, gpa_course)
        session = gpa_course.sessions[0]
        session_service.cancel_session(admin, session, by_teacher=True)
        with pytest.raises(session_service.SessionError):
            session_service.cancel_session(admin, session, by_teacher=False)


class TestRestore:
    """Reversing a cancellation when it was a mistake."""

    def test_restoring_puts_the_session_back_on_the_schedule(
        self, app, db, admin, gpa_course
    ):
        session_service.generate_sessions(None, gpa_course)
        session = gpa_course.sessions[0]
        session_service.cancel_session(admin, session, by_teacher=True, reason="oops")

        session_service.restore_session(admin, session)
        assert session.status is SessionStatus.SCHEDULED
        assert session.cancellation_reason is None
        assert session.needs_attendance is True

    def test_restoring_a_scheduled_session_is_refused(self, app, admin, gpa_course):
        session_service.generate_sessions(None, gpa_course)
        with pytest.raises(session_service.SessionError):
            session_service.restore_session(admin, gpa_course.sessions[0])

    def test_both_cancel_and_restore_land_in_the_audit_trail(
        self, app, db, admin, gpa_course
    ):
        from app.models.audit import AuditLog
        from sqlalchemy import select as _select

        session_service.generate_sessions(None, gpa_course)
        session = gpa_course.sessions[0]
        session_service.cancel_session(admin, session, by_teacher=True)
        session_service.restore_session(admin, session)

        actions = [
            row.action
            for row in db.session.scalars(
                _select(AuditLog).where(AuditLog.entity_type == "session")
            )
        ]
        assert "session.cancel" in actions
        assert "session.restore" in actions


class TestShift:
    """Fixing the start-date of a round after generation."""

    def test_shifting_the_first_session_moves_every_later_session_by_the_same_delta(
        self, app, db, admin, gpa_course
    ):
        session_service.generate_sessions(None, gpa_course)
        original_dates = [s.session_date for s in
                          sorted(gpa_course.sessions, key=lambda x: x.sequence_no)]
        first = gpa_course.sessions[0]

        new_first = first.session_date + timedelta(days=7)
        session_service.shift_sessions_from(admin, first, new_first)

        db.session.refresh(gpa_course)
        shifted = [s.session_date for s in
                   sorted(gpa_course.sessions, key=lambda x: x.sequence_no)]
        assert shifted == [d + timedelta(days=7) for d in original_dates]

    def test_shifting_a_middle_session_leaves_earlier_ones_alone(
        self, app, db, admin, gpa_course
    ):
        session_service.generate_sessions(None, gpa_course)
        ordered = sorted(gpa_course.sessions, key=lambda x: x.sequence_no)
        first_original = ordered[0].session_date
        middle = ordered[3]
        original_middle_date = middle.session_date

        session_service.shift_sessions_from(
            admin, middle, original_middle_date + timedelta(days=2)
        )
        db.session.refresh(gpa_course)
        ordered = sorted(gpa_course.sessions, key=lambda x: x.sequence_no)
        assert ordered[0].session_date == first_original  # unchanged
        assert ordered[3].session_date == original_middle_date + timedelta(days=2)
        assert ordered[4].session_date > original_middle_date  # shifted too

    def test_a_cancelled_session_cannot_be_shifted(self, app, admin, gpa_course):
        session_service.generate_sessions(None, gpa_course)
        session = gpa_course.sessions[0]
        session_service.cancel_session(admin, session, by_teacher=True)
        with pytest.raises(session_service.SessionError):
            session_service.shift_sessions_from(
                admin, session, session.session_date + timedelta(days=7)
            )

    def test_shifting_leaves_held_or_cancelled_sessions_alone(
        self, app, db, admin, gpa_course
    ):
        """Only SCHEDULED rows move — a held session must not be back-dated."""
        session_service.generate_sessions(None, gpa_course)
        ordered = sorted(gpa_course.sessions, key=lambda x: x.sequence_no)

        # Cancel a middle session so it stays put during the shift.
        parked = ordered[5]
        parked_date = parked.session_date
        session_service.cancel_session(admin, parked, by_teacher=False)

        first = ordered[0]
        session_service.shift_sessions_from(
            admin, first, first.session_date + timedelta(days=1)
        )

        db.session.refresh(parked)
        assert parked.session_date == parked_date  # unmoved

    def test_shifting_records_the_offset_in_the_audit_trail(
        self, app, db, admin, gpa_course
    ):
        from app.models.audit import AuditLog
        from sqlalchemy import select as _select

        session_service.generate_sessions(None, gpa_course)
        first = gpa_course.sessions[0]
        session_service.shift_sessions_from(
            admin, first, first.session_date + timedelta(days=3)
        )
        entry = db.session.scalar(
            _select(AuditLog).where(AuditLog.action == "session.shift")
        )
        assert entry is not None
        assert entry.after_json["delta_days"] == 3


class TestRescheduling:
    def test_the_original_is_kept_and_points_at_the_replacement(
        self, app, db, admin, gpa_course
    ):
        """History must show the session moved, not silently change its date."""
        session_service.generate_sessions(None, gpa_course)
        original = gpa_course.sessions[0]
        original_date = original.session_date

        replacement = session_service.reschedule_session(
            admin, original, new_date=original_date + timedelta(days=1), new_start=time(18, 0)
        )

        assert original.status is SessionStatus.RESCHEDULED
        assert original.rescheduled_to_id == replacement.id
        assert original.session_date == original_date  # unchanged
        assert replacement.session_date == original_date + timedelta(days=1)
        assert replacement.start_time == time(18, 0)

    def test_the_replacement_sits_on_no_weekly_slot(self, app, admin, gpa_course):
        session_service.generate_sessions(None, gpa_course)
        replacement = session_service.reschedule_session(
            admin,
            gpa_course.sessions[0],
            new_date=date(2026, 9, 8),
            new_start=time(18, 0),
        )
        assert replacement.slot_id is None

    def test_regenerating_does_not_resurrect_a_moved_session(
        self, app, db, admin, gpa_course
    ):
        """Idempotency is keyed on sequence_no for exactly this reason."""
        session_service.generate_sessions(None, gpa_course)
        session_service.reschedule_session(
            admin,
            gpa_course.sessions[0],
            new_date=date(2026, 9, 8),
            new_start=time(18, 0),
        )
        db.session.refresh(gpa_course)

        created = session_service.generate_sessions(None, gpa_course)
        assert created == []

    def test_a_cancelled_session_cannot_be_moved(self, app, admin, gpa_course):
        session_service.generate_sessions(None, gpa_course)
        session = gpa_course.sessions[0]
        session_service.cancel_session(admin, session, by_teacher=True)
        with pytest.raises(session_service.SessionError):
            session_service.reschedule_session(
                admin, session, new_date=date(2026, 9, 8), new_start=time(18, 0)
            )


class TestMakeupSessions:
    def test_a_makeup_has_no_slot_and_extends_the_sequence(self, app, admin, gpa_course):
        session_service.generate_sessions(None, gpa_course)
        makeup = session_service.add_makeup_session(
            admin, gpa_course, when=date(2026, 10, 10), start_time=time(12, 0)
        )
        assert makeup.slot_id is None
        assert makeup.sequence_no == 9


class TestRoundProgress:
    def test_counts_only_held_sessions(self, app, db, admin, gpa_course):
        session_service.generate_sessions(None, gpa_course)
        held, total = session_service.round_progress(gpa_course)
        assert (held, total) == (0, 8)

        gpa_course.sessions[0].status = SessionStatus.HELD
        db.session.commit()
        held, total = session_service.round_progress(gpa_course)
        assert (held, total) == (1, 8)
