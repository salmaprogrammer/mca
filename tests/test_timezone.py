"""Timezone handling (PLAN.md §2.3).

Two different rules, and mixing them up is the failure mode:

* **Wall-clock** — `session_date`, `start_time`, `end_time`. "Sunday 16:00"
  must stay 16:00 across a DST boundary. Egypt reinstated DST in 2023 (last
  Friday of April → last Thursday of October), so this is live, not theoretical.
* **Instants** — `checked_in_at`, `recorded_at`, `accepted_at`, audit rows.
  Stored as timezone-aware UTC, displayed in Africa/Cairo.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.enums import AttendanceStatus, Role
from app.services import attendance as attendance_service
from app.services import enrollments as enrollment_service
from app.services import sessions as session_service
from tests.conftest import make_course, make_user

SUNDAY = 6
WEDNESDAY = 2
CAIRO = ZoneInfo("Africa/Cairo")

# 2026-04-19 is a Sunday. Egypt's clocks go forward on Friday 2026-04-24,
# so a round starting here straddles the change.
DST_START = date(2026, 4, 19)


class TestWallClockSurvivesDst:
    @pytest.fixture
    def course(self, db, seeded_terms, seeded_course_types, admin):
        teacher = make_user(Role.TEACHER, phone="+201011115001")
        course = make_course(
            admin,
            teacher=teacher,
            course_type_code="gpa_course",  # 2/week, 8 sessions
            slots=[
                {"weekday": SUNDAY, "start_time": "16:00"},
                {"weekday": WEDNESDAY, "start_time": "16:00"},
            ],
            start_date=DST_START,
        )
        session_service.generate_sessions(admin, course)
        return course

    def test_the_round_actually_crosses_the_boundary(self, app, course):
        """Guards the fixture itself — a test that never crosses proves nothing."""
        dates = sorted(s.session_date for s in course.sessions)
        assert dates[0] < date(2026, 4, 24) < dates[-1]

    def test_every_session_keeps_the_same_local_start_time(self, app, course):
        """If these were stored as UTC instants, half would shift by an hour."""
        assert {s.start_time for s in course.sessions} == {time(16, 0)}
        assert {s.end_time for s in course.sessions} == {time(17, 30)}

    def test_the_weekday_never_drifts(self, app, course):
        assert {s.session_date.weekday() for s in course.sessions} == {SUNDAY, WEDNESDAY}

    def test_spacing_stays_exactly_weekly(self, app, course):
        from itertools import pairwise

        sundays = sorted(
            s.session_date for s in course.sessions if s.session_date.weekday() == SUNDAY
        )
        assert {b - a for a, b in pairwise(sundays)} == {timedelta(days=7)}


class TestInstantsAreAwareUtc:
    @pytest.fixture
    def world(self, db, seeded_terms, seeded_course_types, admin):
        teacher = make_user(Role.TEACHER, phone="+201011115002")
        student = make_user(Role.STUDENT, phone="+201055555002")
        course = make_course(
            admin,
            teacher=teacher,
            course_type_code="sat_intermediate",
            slots=[{"weekday": SUNDAY, "start_time": "16:00"}],
            start_date=DST_START,
        )
        enrollment_service.enroll(admin, course, student)
        session_service.generate_sessions(admin, course)
        return admin, course, student

    def test_check_in_comes_back_timezone_aware(self, app, world):
        """SQLite has no timezone type; `UtcDateTime` re-tags on the way out.

        Without it the same column is aware on Postgres and naive on SQLite,
        and `.astimezone()` on the naive value silently assumes the server's
        zone — which is how attendance times end up hours wrong.
        """
        admin, course, student = world
        record = attendance_service.mark_student(
            admin, course.sessions[0], student, AttendanceStatus.PRESENT
        )
        assert record.checked_in_at.tzinfo is not None
        assert record.checked_in_at.utcoffset() == timedelta(0)

    def test_it_survives_a_reload_from_the_database(self, app, db, world):
        admin, course, student = world
        record = attendance_service.mark_student(
            admin, course.sessions[0], student, AttendanceStatus.PRESENT
        )
        record_id = record.id
        db.session.expunge_all()

        from app.models.session import AttendanceRecord

        reloaded = db.session.get(AttendanceRecord, record_id)
        assert reloaded.checked_in_at.tzinfo is not None

    def test_audit_and_terms_timestamps_are_aware_too(self, app, db, world):
        from app.models.audit import AuditLog

        entry = db.session.scalars(db.select(AuditLog)).first()
        assert entry.created_at.tzinfo is not None


class TestCairoDisplay:
    def test_utc_renders_as_cairo_local_time(self, app):
        """16:00 UTC in summer is 18:00 in Cairo (UTC+3 under DST).

        Rendered 12-hour with the active locale's AM/PM marker.
        """
        from app.blueprints.assistant.session_routes import localtime_filter
        from app.formatting import format_time_12h

        summer = datetime(2026, 7, 1, 16, 0, tzinfo=UTC)
        with app.test_request_context():
            assert localtime_filter(summer) == format_time_12h(summer.astimezone(CAIRO))

    def test_winter_and_summer_differ_by_an_hour(self, app):
        """Proves the offset is looked up, not hardcoded to +2."""
        winter = datetime(2026, 1, 15, 12, 0, tzinfo=UTC).astimezone(CAIRO)
        summer = datetime(2026, 7, 15, 12, 0, tzinfo=UTC).astimezone(CAIRO)
        assert summer.utcoffset() - winter.utcoffset() == timedelta(hours=1)

    def test_missing_timestamps_render_as_a_dash(self, app):
        from app.blueprints.assistant.session_routes import localtime_filter

        with app.test_request_context():
            assert localtime_filter(None) == "—"
