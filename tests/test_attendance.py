"""Sprints S3.3, S3.4, S3.5 — attendance marking, scoping and history."""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.enums import AttendanceStatus, Role, SessionStatus
from app.services import attendance as attendance_service
from app.services import enrollments as enrollment_service
from app.services import sessions as session_service
from tests.conftest import accept_current_terms, link_parent, login, make_course, make_user

SUNDAY = 6
WEDNESDAY = 2
START = date(2026, 9, 6)


@pytest.fixture
def world(app, db, seeded_terms, seeded_course_types, admin):
    """One course, one teacher, two enrolled students, sessions generated."""
    teacher = make_user(Role.TEACHER, phone="+201011116001", name="Ahmed Fathy")
    accept_current_terms(teacher)
    student_a = make_user(Role.STUDENT, phone="+201055556001", name="Youssef")
    student_b = make_user(Role.STUDENT, phone="+201055556002", name="Nour")
    for s in (student_a, student_b):
        accept_current_terms(s)
    parent = make_user(Role.PARENT, phone="+201099996001", name="Adel")
    link_parent(parent, student_a)
    accept_current_terms(parent)

    course = make_course(
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
    enrollment_service.enroll(admin, course, student_a)
    enrollment_service.enroll(admin, course, student_b)
    session_service.generate_sessions(admin, course)

    return {
        "admin": admin,
        "teacher": teacher,
        "student_a": student_a,
        "student_b": student_b,
        "parent": parent,
        "course": course,
        "session": course.sessions[0],
    }


class TestMarkingStudents:
    def test_present_records_a_real_timestamp(self, app, world):
        """The brief insists on time of check-in, not just a flag."""
        record = attendance_service.mark_student(
            world["admin"], world["session"], world["student_a"], AttendanceStatus.PRESENT
        )
        assert record.status is AttendanceStatus.PRESENT
        assert record.checked_in_at is not None
        assert record.checked_in_at.tzinfo is not None  # aware UTC, per §2.3

    def test_absent_carries_no_check_in_time(self, app, world):
        """Stamping an arrival time on someone who never arrived is nonsense."""
        record = attendance_service.mark_student(
            world["admin"], world["session"], world["student_a"], AttendanceStatus.ABSENT
        )
        assert record.checked_in_at is None

    def test_late_still_counts_as_arriving(self, app, world):
        record = attendance_service.mark_student(
            world["admin"], world["session"], world["student_a"], AttendanceStatus.LATE
        )
        assert record.checked_in_at is not None
        assert record.was_present is True

    def test_marking_flips_the_session_to_held(self, app, world):
        assert world["session"].status is SessionStatus.SCHEDULED
        attendance_service.mark_student(
            world["admin"], world["session"], world["student_a"], AttendanceStatus.PRESENT
        )
        assert world["session"].status is SessionStatus.HELD

    def test_re_marking_updates_rather_than_duplicating(self, app, db, world):
        """One authoritative record per student per session, plus its history."""
        attendance_service.mark_student(
            world["admin"], world["session"], world["student_a"], AttendanceStatus.ABSENT
        )
        attendance_service.mark_student(
            world["admin"], world["session"], world["student_a"], AttendanceStatus.PRESENT
        )
        db.session.refresh(world["session"])

        records = [
            r for r in world["session"].attendance if r.student_id == world["student_a"].id
        ]
        assert len(records) == 1
        assert records[0].status is AttendanceStatus.PRESENT

    def test_an_amendment_is_audited_separately(self, app, db, world):
        attendance_service.mark_student(
            world["admin"], world["session"], world["student_a"], AttendanceStatus.ABSENT
        )
        attendance_service.mark_student(
            world["admin"], world["session"], world["student_a"], AttendanceStatus.PRESENT
        )
        actions = [
            e.action
            for e in db.session.scalars(
                select(AuditLog).where(AuditLog.action.like("attendance.%"))
            )
        ]
        assert "attendance.mark" in actions
        assert "attendance.amend" in actions

    def test_a_student_not_enrolled_is_refused(self, app, db, world):
        outsider = make_user(Role.STUDENT, phone="+201055556099", name="Outsider")
        with pytest.raises(attendance_service.AttendanceError):
            attendance_service.mark_student(
                world["admin"], world["session"], outsider, AttendanceStatus.PRESENT
            )

    def test_a_cancelled_session_cannot_be_marked(self, app, world):
        session_service.cancel_session(world["admin"], world["session"], by_teacher=True)
        with pytest.raises(attendance_service.AttendanceError):
            attendance_service.mark_student(
                world["admin"], world["session"], world["student_a"], AttendanceStatus.PRESENT
            )

    def test_a_moved_session_points_at_its_replacement(self, app, world):
        from flask_babel import force_locale

        session_service.reschedule_session(
            world["admin"], world["session"], new_date=date(2026, 9, 8), new_start=time(18, 0)
        )
        with force_locale("en"), pytest.raises(attendance_service.AttendanceError) as exc:
            attendance_service.mark_student(
                world["admin"], world["session"], world["student_a"], AttendanceStatus.PRESENT
            )
        assert "replacement" in str(exc.value).lower()


class TestMarkingTeachers:
    def test_teacher_attendance_lives_on_the_session(self, app, world):
        attendance_service.mark_teacher(
            world["admin"], world["session"], AttendanceStatus.PRESENT
        )
        assert world["session"].teacher_status is AttendanceStatus.PRESENT
        assert world["session"].teacher_checked_in_at is not None

    def test_teacher_absence_carries_no_check_in(self, app, world):
        attendance_service.mark_teacher(
            world["admin"], world["session"], AttendanceStatus.ABSENT
        )
        assert world["session"].teacher_checked_in_at is None

    def test_it_records_who_marked_it(self, app, world):
        attendance_service.mark_teacher(
            world["admin"], world["session"], AttendanceStatus.PRESENT
        )
        assert world["session"].teacher_recorded_by_id == world["admin"].id


class TestRouteScoping:
    """The wall the brief cares most about, exercised over HTTP."""

    @pytest.fixture
    def other(self, app, db, seeded_terms, seeded_course_types, admin):
        """A second teacher with their own course, session and student."""
        teacher = make_user(Role.TEACHER, phone="+201011116099", name="Sara")
        accept_current_terms(teacher)
        student = make_user(Role.STUDENT, phone="+201055556098", name="Someone Else")
        course = make_course(
            admin,
            teacher=teacher,
            name="Other course",
            course_type_code="est_basics",
            slots=[
                {"weekday": 0, "start_time": "10:00"},
                {"weekday": 1, "start_time": "10:00"},
            ],
            start_date=START,
        )
        enrollment_service.enroll(admin, course, student)
        session_service.generate_sessions(admin, course)
        return {"teacher": teacher, "course": course, "session": course.sessions[0]}

    def test_a_teacher_can_mark_their_own_session(self, app, world):
        client = app.test_client()
        login(client, world["teacher"])
        response = client.post(
            f"/teacher/sessions/{world['session'].id}"
            f"/students/{world['student_a'].id}/mark",
            data={"status": "present"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert world["session"].attendance_for(world["student_a"].id) is not None

    def test_a_teacher_gets_404_for_another_teachers_session(self, app, world, other):
        """404, not 403 — a 403 would confirm the session ID exists."""
        client = app.test_client()
        login(client, world["teacher"])
        assert client.get(f"/teacher/sessions/{other['session'].id}").status_code == 404

    def test_a_teacher_cannot_mark_into_another_teachers_session(self, app, world, other):
        client = app.test_client()
        login(client, world["teacher"])
        response = client.post(
            f"/teacher/sessions/{other['session'].id}"
            f"/students/{world['student_a'].id}/mark",
            data={"status": "present"},
        )
        assert response.status_code == 404
        assert other["session"].attendance == []

    def test_a_teacher_cannot_mark_a_student_outside_their_courses(self, app, world, other):
        """Own session, but somebody else's student."""
        outsider = make_user(Role.STUDENT, phone="+201055556097")
        client = app.test_client()
        login(client, world["teacher"])
        response = client.post(
            f"/teacher/sessions/{world['session'].id}/students/{outsider.id}/mark",
            data={"status": "present"},
        )
        assert response.status_code == 404

    def test_a_teacher_cannot_reach_the_staff_session_page(self, app, world):
        client = app.test_client()
        login(client, world["teacher"])
        assert (
            client.get(f"/assistant/sessions/{world['session'].id}").status_code == 403
        )

    def test_a_student_cannot_mark_attendance_at_all(self, app, world):
        client = app.test_client()
        login(client, world["student_a"])
        response = client.post(
            f"/teacher/sessions/{world['session'].id}"
            f"/students/{world['student_a'].id}/mark",
            data={"status": "present"},
        )
        assert response.status_code == 403

    def test_the_portal_exposes_no_attendance_mutation(self, app):
        """S5.4's guarantee: the portal blueprint has no write routes."""
        mutating = [
            rule.rule
            for rule in app.url_map.iter_rules()
            if rule.rule.startswith("/portal")
            and {"POST", "PATCH", "PUT", "DELETE"} & rule.methods
        ]
        # Only the parent's child switcher, which writes to the session cookie.
        assert mutating == ["/portal/child/<int:student_id>/select"]


class TestHistory:
    def test_a_student_sees_their_own_records_newest_first(self, app, world):
        for index in (0, 1, 2):
            attendance_service.mark_student(
                world["admin"],
                world["course"].sessions[index],
                world["student_a"],
                AttendanceStatus.PRESENT,
            )
        records = attendance_service.history_for_student(world["student_a"])
        assert len(records) == 3
        dates = [r.session.session_date for r in records]
        assert dates == sorted(dates, reverse=True)

    def test_history_never_leaks_another_student(self, app, world):
        attendance_service.mark_student(
            world["admin"], world["session"], world["student_b"], AttendanceStatus.ABSENT
        )
        records = attendance_service.history_for_student(world["student_a"])
        assert records == []

    def test_a_date_range_filters(self, app, world):
        for index in (0, 1, 2, 3):
            attendance_service.mark_student(
                world["admin"],
                world["course"].sessions[index],
                world["student_a"],
                AttendanceStatus.PRESENT,
            )
        cutoff = START + timedelta(days=7)
        records = attendance_service.history_for_student(world["student_a"], since=cutoff)
        assert all(r.session.session_date >= cutoff for r in records)
        assert len(records) < 4

    def test_the_course_direction_is_queryable_too(self, app, world):
        """The brief requires both directions: per student and per course."""
        attendance_service.mark_student(
            world["admin"], world["session"], world["student_a"], AttendanceStatus.PRESENT
        )
        sessions = attendance_service.history_for_courses([world["course"].id])
        assert len(sessions) == 8
        marked = [s for s in sessions if s.attendance]
        assert len(marked) == 1

    def test_filtering_by_teacher_works(self, app, world):
        sessions = attendance_service.history_for_courses(
            [world["course"].id], teacher_id=world["teacher"].id
        )
        assert len(sessions) == 8
        assert attendance_service.history_for_courses(
            [world["course"].id], teacher_id=world["admin"].id
        ) == []

    def test_the_summary_counts_each_status(self, app, world):
        attendance_service.mark_student(
            world["admin"], world["course"].sessions[0], world["student_a"],
            AttendanceStatus.PRESENT,
        )
        attendance_service.mark_student(
            world["admin"], world["course"].sessions[1], world["student_a"],
            AttendanceStatus.ABSENT,
        )
        summary = attendance_service.summarise(
            attendance_service.history_for_student(world["student_a"])
        )
        assert summary["present"] == 1
        assert summary["absent"] == 1
        assert summary["late"] == 0


class TestPortalRendering:
    """Pages fetched with real data, not just the service beneath them."""

    def _get(self, app, user, url):
        client = app.test_client()
        login(client, user)
        response = client.get(url)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        return response.get_data(as_text=True)

    def test_student_sees_their_attendance(self, app, world):
        attendance_service.mark_student(
            world["admin"], world["session"], world["student_a"], AttendanceStatus.PRESENT
        )
        body = self._get(app, world["student_a"], "/portal/attendance")
        assert str(world["session"].session_date) in body

    def test_parent_sees_their_childs_attendance(self, app, world):
        attendance_service.mark_student(
            world["admin"], world["session"], world["student_a"], AttendanceStatus.PRESENT
        )
        body = self._get(app, world["parent"], "/portal/attendance")
        assert str(world["session"].session_date) in body

    def test_staff_session_and_log_pages_render(self, app, world):
        attendance_service.mark_student(
            world["admin"], world["session"], world["student_a"], AttendanceStatus.PRESENT
        )
        assert "Youssef" in self._get(
            app, world["admin"], f"/assistant/sessions/{world['session'].id}"
        )
        assert "Youssef" in self._get(app, world["admin"], "/assistant/attendance")
        self._get(app, world["admin"], "/assistant/sessions")
        self._get(
            app, world["admin"], f"/assistant/courses/{world['course'].id}/sessions"
        )

    def test_teacher_pages_render(self, app, world):
        assert self._get(app, world["teacher"], "/teacher/")
        self._get(app, world["teacher"], f"/teacher/sessions/{world['session'].id}")
        self._get(app, world["teacher"], f"/teacher/courses/{world['course'].id}")
