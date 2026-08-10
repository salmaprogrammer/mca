"""Sprints S7.1–S7.3 — role landing dashboards.

A dashboard answers "what needs my attention", so the tests are mostly about
two things: the counts are right, and a dashboard cannot surface a row the
viewer could not reach any other way.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.models.enums import AttendanceStatus, PaymentStatus, Role
from app.services import attendance as attendance_service
from app.services import dashboard as dashboard_service
from app.services import enrollments as enrollment_service
from app.services import sessions as session_service
from app.services import teaching as teaching_service
from app.services import whatsapp as whatsapp_service
from tests.conftest import accept_current_terms, link_parent, login, make_course, make_user

SUNDAY = 6
WEDNESDAY = 2
START = date(2026, 9, 6)


@pytest.fixture
def world(app, db, seeded_terms, seeded_course_types, admin, assistant):
    teacher = make_user(Role.TEACHER, phone="+201011110001", name="Ahmed")
    accept_current_terms(teacher)
    student_a = make_user(Role.STUDENT, phone="+201055550001", name="Youssef")
    student_b = make_user(Role.STUDENT, phone="+201055550002", name="Nour")
    for s in (student_a, student_b):
        accept_current_terms(s)
    parent = make_user(Role.PARENT, phone="+201099990001", name="Adel")
    link_parent(parent, student_a)
    accept_current_terms(parent)

    course = make_course(
        admin,
        teacher=teacher,
        name="Nov round",
        course_type_code="gpa_course",
        slots=[
            {"weekday": SUNDAY, "start_time": "16:00"},
            {"weekday": WEDNESDAY, "start_time": "16:00"},
        ],
        price_egp=900,
        start_date=START,
    )
    enrollment_service.enroll(admin, course, student_a)
    enrollment_service.enroll(admin, course, student_b)
    session_service.generate_sessions(admin, course)

    return {
        "admin": admin,
        "assistant": assistant,
        "teacher": teacher,
        "student_a": student_a,
        "student_b": student_b,
        "parent": parent,
        "course": course,
    }


class TestStaffDashboard:
    def test_it_counts_todays_sessions(self, app, world):
        dash = dashboard_service.staff_dashboard(world["admin"], on=START)
        assert len(dash.sessions_today) == 1

    def test_a_partly_marked_session_still_needs_attention(self, app, world):
        """Two students enrolled, one marked — the register is not done."""
        session = world["course"].sessions[0]
        attendance_service.mark_student(
            world["admin"], session, world["student_a"], AttendanceStatus.PRESENT
        )
        dash = dashboard_service.staff_dashboard(world["admin"], on=START)
        assert len(dash.unmarked_sessions) == 1

    def test_a_fully_marked_session_drops_off(self, app, world):
        session = world["course"].sessions[0]
        for student in (world["student_a"], world["student_b"]):
            attendance_service.mark_student(
                world["admin"], session, student, AttendanceStatus.PRESENT
            )
        dash = dashboard_service.staff_dashboard(world["admin"], on=START)
        assert dash.unmarked_sessions == []

    def test_a_cancelled_session_is_not_chased(self, app, world):
        session_service.cancel_session(
            world["admin"], world["course"].sessions[0], by_teacher=True
        )
        dash = dashboard_service.staff_dashboard(world["admin"], on=START)
        assert dash.unmarked_sessions == []

    def test_it_lists_students_still_owed_an_update(self, app, world):
        dash = dashboard_service.staff_dashboard(world["admin"], on=START)
        assert len(dash.students_without_update) == 2

        whatsapp_service.hand_off(world["admin"], world["student_a"], on=START)
        dash = dashboard_service.staff_dashboard(world["admin"], on=START)
        assert [s.full_name for s in dash.students_without_update] == ["Nour"]

    def test_a_prepared_update_does_not_count_as_delivered(self, app, world):
        """A message nobody opened means the family has heard nothing."""
        whatsapp_service.prepare_daily_update(
            world["admin"], world["student_a"], on=START
        )
        dash = dashboard_service.staff_dashboard(world["admin"], on=START)
        assert len(dash.students_without_update) == 2

    def test_it_totals_what_is_outstanding(self, app, world):
        dash = dashboard_service.staff_dashboard(world["admin"], on=START)
        assert len(dash.unpaid) == 2
        assert dash.outstanding_total == 1800

        enrollment_service.set_payment_status(
            world["admin"], world["course"].enrollments[0], PaymentStatus.PAID
        )
        dash = dashboard_service.staff_dashboard(world["admin"], on=START)
        assert dash.outstanding_total == 900

    def test_needs_attention_is_false_on_a_clean_day(self, app, db, world, admin):
        """No sessions, everyone messaged, nothing left waiting."""
        quiet = START + timedelta(days=1)  # a Monday with no session
        for student in (world["student_a"], world["student_b"]):
            whatsapp_service.hand_off(admin, student, on=quiet)
        dash = dashboard_service.staff_dashboard(admin, on=quiet)
        assert dash.needs_attention is False

    def test_updates_written_but_never_sent_are_surfaced(self, app, world):
        """With no API there are no delivery failures — but there is a new way
        to let a family down, and the dashboard has to name it."""
        whatsapp_service.prepare_daily_update(
            world["admin"], world["student_a"], on=START
        )
        dash = dashboard_service.staff_dashboard(world["admin"], on=START)
        assert len(dash.unsent_messages) == 1
        assert dash.needs_attention is True


class TestTeacherDashboard:
    def test_it_shows_only_this_teachers_work(self, app, db, world, admin):
        other_teacher = make_user(Role.TEACHER, phone="+201011110099", name="Sara")
        make_course(
            admin,
            teacher=other_teacher,
            name="Other",
            course_type_code="sat_intermediate",
            slots=[{"weekday": SUNDAY, "start_time": "09:00"}],
            start_date=START,
        )
        dash = dashboard_service.teacher_dashboard(world["teacher"], on=START)
        assert [c.name for c in dash.courses] == ["Nov round"]
        assert all(s.course_id == world["course"].id for s in dash.sessions_today)

    def test_the_next_session_is_the_soonest_scheduled_one(self, app, world):
        dash = dashboard_service.teacher_dashboard(world["teacher"], on=START)
        assert dash.next_session is not None
        assert dash.next_session.session_date == START

    def test_a_cancelled_session_is_never_the_next_one(self, app, world):
        session_service.cancel_session(
            world["admin"], world["course"].sessions[0], by_teacher=True
        )
        dash = dashboard_service.teacher_dashboard(world["teacher"], on=START)
        assert dash.next_session.session_date > START

    def test_the_week_grid_starts_on_saturday(self, app, world):
        dash = dashboard_service.teacher_dashboard(world["teacher"], on=START)
        assert next(day for day, _entries in dash.week) == 5  # Saturday


class TestPortalDashboard:
    def test_a_parent_sees_their_own_childs_figures(self, app, world):
        session = world["course"].sessions[0]
        attendance_service.mark_student(
            world["admin"], session, world["student_a"], AttendanceStatus.PRESENT
        )
        dash = dashboard_service.portal_dashboard(world["parent"], world["student_a"])
        assert dash.summary["present"] == 1
        assert dash.next_session is not None

    def test_it_never_leaks_a_classmates_feedback(self, app, world):
        teaching_service.add_feedback(
            world["admin"], world["course"], world["student_b"],
            text="CLASSMATESECRET", for_date=START,
        )
        dash = dashboard_service.portal_dashboard(world["parent"], world["student_a"])
        assert all("CLASSMATESECRET" not in f.text for f in dash.feedback)

    def test_it_surfaces_what_the_family_owes(self, app, world):
        dash = dashboard_service.portal_dashboard(world["parent"], world["student_a"])
        assert len(dash.unpaid) == 1
        assert dash.unpaid[0].amount_due == 900

    def test_paying_clears_it(self, app, world):
        enrollment = world["course"].enrollments[0]
        enrollment_service.set_payment_status(
            world["admin"], enrollment, PaymentStatus.PAID
        )
        dash = dashboard_service.portal_dashboard(world["parent"], world["student_a"])
        assert dash.unpaid == []

    def test_no_linked_student_is_handled(self, app, db, world, seeded_terms):
        lonely_parent = make_user(Role.PARENT, phone="+201099990099")
        dash = dashboard_service.portal_dashboard(lonely_parent, None)
        assert dash.student is None
        assert dash.courses == []


class TestRendering:
    """Every landing page, fetched with real data in it."""

    def _body(self, app, user, url="/"):
        client = app.test_client()
        login(client, user)
        response = client.get(url, follow_redirects=True)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        return response.get_data(as_text=True)

    def test_admin_overview_renders(self, app, world):
        body = self._body(app, world["admin"], "/admin/")
        assert "1800" in body or "١٨٠٠" in body  # outstanding total

    def test_assistant_home_renders(self, app, world):
        body = self._body(app, world["assistant"], "/assistant/")
        assert "Youssef" in body  # a student still owed an update

    def test_teacher_home_renders(self, app, world):
        body = self._body(app, world["teacher"], "/teacher/")
        assert "Nov round" in body or "GPA" in body

    def test_parent_portal_renders(self, app, world):
        body = self._body(app, world["parent"], "/portal/")
        assert "Youssef" in body

    def test_student_portal_renders(self, app, world):
        body = self._body(app, world["student_a"], "/portal/")
        assert "Youssef" in body

    def test_the_direct_child_link_still_renders(self, app, world):
        body = self._body(
            app, world["parent"], f"/portal/students/{world['student_a'].id}"
        )
        assert "Youssef" in body

    def test_dashboards_render_in_english_too(self, app, world):
        client = app.test_client()
        login(client, world["admin"])
        client.post("/me/language", data={"locale": "en", "next": "/admin/"})
        body = client.get("/admin/").get_data(as_text=True)
        assert "Total outstanding" in body


class TestResponsive:
    def test_the_stylesheet_has_a_mobile_breakpoint(self):
        """Parents open the portal on a phone; staff use a desktop."""
        from pathlib import Path

        css = (
            Path(__file__).resolve().parent.parent
            / "app" / "static" / "css" / "app.css"
        ).read_text(encoding="utf-8")
        assert "@media (max-width: 720px)" in css
        # The grids that would otherwise force horizontal scrolling.
        mobile_block = css.split("@media (max-width: 720px)")[-1]
        for selector in (".stat-grid", ".card-grid", ".two-col", ".week-grid"):
            assert selector in mobile_block, f"{selector} not collapsed on mobile"


TEMPLATE_DASHBOARDS = {
    "assistant/home.html": dashboard_service.StaffDashboard,
    "admin/overview.html": dashboard_service.StaffDashboard,
    "teacher/home.html": dashboard_service.TeacherDashboard,
    "portal/home.html": dashboard_service.PortalDashboard,
}

DASH_ATTRIBUTE = re.compile(r"\bdash\.([a-z_][a-z0-9_]*)")
TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "app" / "templates"


def _readable_names(dataclass_type) -> set[str]:
    return {f.name for f in dataclasses.fields(dataclass_type)} | {
        name
        for name, value in vars(dataclass_type).items()
        if isinstance(value, property)
    }


class TestTemplatesMatchTheirDashboard:
    """Renaming a dashboard field breaks its template in total silence.

    Jinja resolves an unknown attribute to Undefined, which is falsy — so an
    `action_panel` keyed on it simply stops appearing. That is exactly how the
    "delivery failed" panel survived the WhatsApp rewrite: still in the
    template, pointing at a field that no longer existed, showing nothing and
    erroring nowhere. This walks the templates instead of trusting them.
    """

    @pytest.mark.parametrize("template_name", sorted(TEMPLATE_DASHBOARDS))
    def test_every_dash_attribute_exists(self, template_name):
        text = (TEMPLATE_ROOT / template_name).read_text(encoding="utf-8")
        used = set(DASH_ATTRIBUTE.findall(text))
        known = _readable_names(TEMPLATE_DASHBOARDS[template_name])

        unknown = sorted(used - known)
        assert not unknown, (
            f"{template_name} reads dash.{{{','.join(unknown)}}}, "
            f"which {TEMPLATE_DASHBOARDS[template_name].__name__} does not define"
        )

    def test_the_scan_finds_something(self):
        """Guards the regex: a pattern matching nothing would pass silently."""
        text = (TEMPLATE_ROOT / "assistant" / "home.html").read_text(encoding="utf-8")
        assert len(set(DASH_ATTRIBUTE.findall(text))) > 4
