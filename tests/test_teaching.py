"""Sprints S4.1–S4.3 — homework, feedback and materials.

Feedback is the tightest visibility scope in the system: one student and their
linked parents, plus staff and the course's own teacher. Most of this file
exists to prove a classmate cannot read it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.enums import Role
from app.services import courses as course_service
from app.services import enrollments as enrollment_service
from app.services import teaching as teaching_service
from app.services.scoping import feedback_for, homework_for, materials_for
from tests.conftest import accept_current_terms, link_parent, login, make_course, make_user

SUNDAY = 6
WEDNESDAY = 2


@pytest.fixture
def world(app, db, seeded_terms, seeded_course_types, admin):
    """One course, two classmates, each with their own parent."""
    teacher = make_user(Role.TEACHER, phone="+201011114001", name="Ahmed")
    accept_current_terms(teacher)

    student_a = make_user(Role.STUDENT, phone="+201055554001", name="Youssef")
    student_b = make_user(Role.STUDENT, phone="+201055554002", name="Nour")
    parent_a = make_user(Role.PARENT, phone="+201099994001", name="Adel")
    parent_b = make_user(Role.PARENT, phone="+201099994002", name="Hassan")
    for u in (student_a, student_b, parent_a, parent_b):
        accept_current_terms(u)
    link_parent(parent_a, student_a)
    link_parent(parent_b, student_b)

    course = make_course(
        admin,
        teacher=teacher,
        name="Nov round",
        course_type_code="gpa_course",
        slots=[
            {"weekday": SUNDAY, "start_time": "16:00"},
            {"weekday": WEDNESDAY, "start_time": "16:00"},
        ],
    )
    enrollment_service.enroll(admin, course, student_a)
    enrollment_service.enroll(admin, course, student_b)

    return {
        "admin": admin,
        "teacher": teacher,
        "student_a": student_a,
        "student_b": student_b,
        "parent_a": parent_a,
        "parent_b": parent_b,
        "course": course,
    }


class TestHomework:
    def test_it_is_visible_to_every_enrolled_student(self, app, world):
        """Homework is course-wide, unlike feedback."""
        teaching_service.add_homework(
            world["admin"], world["course"], text="Worksheet 3, questions 1-10."
        )
        for student in (world["student_a"], world["student_b"]):
            items = homework_for(student)
            assert len(items) == 1
            assert "Worksheet 3" in items[0].text

    def test_parents_see_their_childs_homework(self, app, world):
        teaching_service.add_homework(world["admin"], world["course"], text="Read chapter 4.")
        assert len(homework_for(world["parent_a"])) == 1

    def test_a_student_outside_the_course_sees_nothing(self, app, db, world):
        teaching_service.add_homework(world["admin"], world["course"], text="Read chapter 4.")
        outsider = make_user(Role.STUDENT, phone="+201055554099")
        assert homework_for(outsider) == []

    def test_the_teacher_sees_their_own_courses_homework(self, app, world):
        teaching_service.add_homework(world["admin"], world["course"], text="Read chapter 4.")
        assert len(homework_for(world["teacher"])) == 1

    def test_empty_text_is_refused(self, app, world):
        with pytest.raises(teaching_service.TeachingError):
            teaching_service.add_homework(world["admin"], world["course"], text="   ")

    def test_it_defaults_to_today(self, app, world):
        item = teaching_service.add_homework(world["admin"], world["course"], text="x")
        assert item.for_date == date.today()

    def test_creation_is_audited(self, app, db, world):
        item = teaching_service.add_homework(world["admin"], world["course"], text="x")
        entry = db.session.scalar(
            select(AuditLog).where(
                AuditLog.action == "homework.create", AuditLog.entity_id == str(item.id)
            )
        )
        assert entry is not None and entry.actor_id == world["admin"].id


class TestFeedbackPrivacy:
    """The tightest scope in the app."""

    @pytest.fixture
    def written(self, app, world):
        return teaching_service.add_feedback(
            world["admin"],
            world["course"],
            world["student_a"],
            text="Great participation, needs practice on quadratics.",
        )

    def test_the_student_it_is_about_can_read_it(self, app, world, written):
        items = feedback_for(world["student_a"])
        assert len(items) == 1
        assert "quadratics" in items[0].text

    def test_their_parent_can_read_it(self, app, world, written):
        assert len(feedback_for(world["parent_a"])) == 1

    def test_a_classmate_cannot_read_it(self, app, world, written):
        """Student B is in the same course and must still see nothing.

        Course visibility is not sufficient for feedback — this is the check
        that distinguishes it from homework.
        """
        assert feedback_for(world["student_b"]) == []

    def test_the_classmates_parent_cannot_read_it(self, app, world, written):
        assert feedback_for(world["parent_b"]) == []

    def test_a_parent_cannot_request_another_familys_child(self, app, world, written):
        from werkzeug.exceptions import NotFound

        with pytest.raises(NotFound):
            feedback_for(world["parent_b"], student=world["student_a"])

    def test_a_student_cannot_request_a_classmate(self, app, world, written):
        from werkzeug.exceptions import NotFound

        with pytest.raises(NotFound):
            feedback_for(world["student_b"], student=world["student_a"])

    def test_staff_and_the_courses_teacher_can_read_it(self, app, world, written):
        assert len(feedback_for(world["admin"])) == 1
        assert len(feedback_for(world["teacher"])) == 1

    def test_another_teacher_cannot_read_it(self, app, db, world, written, admin):
        other_teacher = make_user(Role.TEACHER, phone="+201011114099", name="Sara")
        make_course(
            admin,
            teacher=other_teacher,
            name="Other",
            course_type_code="sat_intermediate",
            slots=[{"weekday": 0, "start_time": "09:00"}],
        )
        assert feedback_for(other_teacher) == []

    def test_feedback_about_an_unenrolled_student_is_refused(self, app, db, world):
        outsider = make_user(Role.STUDENT, phone="+201055554098")
        with pytest.raises(teaching_service.TeachingError):
            teaching_service.add_feedback(
                world["admin"], world["course"], outsider, text="x"
            )

    def test_feedback_can_only_be_about_a_student(self, app, world):
        with pytest.raises(teaching_service.TeachingError):
            teaching_service.add_feedback(
                world["admin"], world["course"], world["teacher"], text="x"
            )


class TestFeedbackOverHttp:
    """Same privacy rule, exercised through real requests."""

    @pytest.fixture
    def written(self, app, world):
        return teaching_service.add_feedback(
            world["admin"], world["course"], world["student_a"], text="SECRETFEEDBACK"
        )

    def _body(self, app, user, url):
        client = app.test_client()
        login(client, user)
        response = client.get(url)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        return response.get_data(as_text=True)

    def test_it_appears_for_the_right_family(self, app, world, written):
        assert "SECRETFEEDBACK" in self._body(app, world["student_a"], "/portal/")
        assert "SECRETFEEDBACK" in self._body(app, world["parent_a"], "/portal/")

    def test_it_never_appears_for_a_classmate(self, app, world, written):
        assert "SECRETFEEDBACK" not in self._body(app, world["student_b"], "/portal/")
        assert "SECRETFEEDBACK" not in self._body(app, world["parent_b"], "/portal/")

    def test_it_never_leaks_through_the_shared_course_page(self, app, world, written):
        """Both students can open this course — only one may see the feedback."""
        url = f"/portal/courses/{world['course'].id}"
        assert "SECRETFEEDBACK" in self._body(app, world["student_a"], url)
        assert "SECRETFEEDBACK" not in self._body(app, world["student_b"], url)

    def test_a_parent_switching_children_does_not_mix_feedback(self, app, db, world):
        """One parent, two children: viewing one must not show the other's."""
        second_child = make_user(Role.STUDENT, phone="+201055554050", name="Sibling")
        accept_current_terms(second_child)
        link_parent(world["parent_a"], second_child)
        enrollment_service.enroll(world["admin"], world["course"], second_child)

        teaching_service.add_feedback(
            world["admin"], world["course"], world["student_a"], text="FIRSTCHILD"
        )
        teaching_service.add_feedback(
            world["admin"], world["course"], second_child, text="SECONDCHILD"
        )

        client = app.test_client()
        login(client, world["parent_a"])

        client.post(f"/portal/child/{world['student_a'].id}/select")
        body = client.get("/portal/").get_data(as_text=True)
        assert "FIRSTCHILD" in body and "SECONDCHILD" not in body

        client.post(f"/portal/child/{second_child.id}/select")
        body = client.get("/portal/").get_data(as_text=True)
        assert "SECONDCHILD" in body and "FIRSTCHILD" not in body

    def test_students_cannot_reach_the_staff_feedback_page(self, app, world):
        client = app.test_client()
        login(client, world["student_a"])
        assert (
            client.get(f"/assistant/courses/{world['course'].id}/feedback").status_code
            == 403
        )

    def test_a_teacher_cannot_reach_the_staff_feedback_page(self, app, world):
        client = app.test_client()
        login(client, world["teacher"])
        assert (
            client.get(f"/assistant/courses/{world['course'].id}/feedback").status_code
            == 403
        )


class TestMaterials:
    def test_a_teacher_adds_links_to_their_own_course(self, app, world):
        teaching_service.add_material(
            world["teacher"], world["course"], title="Chapter 3 slides",
            url="https://example.com/ch3",
        )
        assert len(materials_for(world["student_a"])) == 1

    def test_parents_see_them_too(self, app, world):
        teaching_service.add_material(
            world["teacher"], world["course"], title="Slides", url="https://example.com/x"
        )
        assert len(materials_for(world["parent_a"])) == 1

    @pytest.mark.parametrize(
        "bad_url",
        [
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "file:///etc/passwd",
            "not a url",
            "",
            "   ",
        ],
    )
    def test_dangerous_or_malformed_links_are_refused(self, app, world, bad_url):
        """A material link is rendered as an anchor a student will click."""
        with pytest.raises(teaching_service.TeachingError):
            teaching_service.add_material(
                world["teacher"], world["course"], title="Bad", url=bad_url
            )

    @pytest.mark.parametrize(
        "good_url",
        ["https://example.com/a", "http://example.com", "https://sub.example.co.uk/x?y=1"],
    )
    def test_http_and_https_links_are_accepted(self, app, world, good_url):
        material = teaching_service.add_material(
            world["teacher"], world["course"], title="Fine", url=good_url
        )
        assert material.url == good_url

    def test_an_over_long_link_is_refused(self, app, world):
        with pytest.raises(teaching_service.TeachingError):
            teaching_service.add_material(
                world["teacher"],
                world["course"],
                title="Long",
                url="https://example.com/" + "a" * 3000,
            )

    def test_links_render_with_noopener(self, app, world):
        """Teacher-supplied links leave the app; they must not get window.opener.

        Materials are per course, so they render on the course page rather than
        the portal landing page.
        """
        teaching_service.add_material(
            world["teacher"], world["course"], title="Slides", url="https://example.com/x"
        )
        client = app.test_client()
        login(client, world["student_a"])
        body = client.get(
            f"/portal/courses/{world['course'].id}"
        ).get_data(as_text=True)
        assert "Slides" in body
        assert 'rel="noopener noreferrer"' in body

    def test_a_teacher_cannot_add_to_another_teachers_course(self, app, db, world, admin):
        other_teacher = make_user(Role.TEACHER, phone="+201011114098")
        accept_current_terms(other_teacher)
        client = app.test_client()
        login(client, other_teacher)
        response = client.post(
            f"/teacher/courses/{world['course'].id}/materials",
            data={"title": "Sneaky", "url": "https://example.com"},
        )
        assert response.status_code == 404
        assert world["course"].materials == []


class TestStaffPagesRender:
    def _body(self, app, user, url):
        client = app.test_client()
        login(client, user)
        response = client.get(url)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        return response.get_data(as_text=True)

    def test_all_three_staff_pages_render_with_data(self, app, world):
        course_id = world["course"].id
        teaching_service.add_homework(world["admin"], world["course"], text="HWTEXT")
        teaching_service.add_feedback(
            world["admin"], world["course"], world["student_a"], text="FBTEXT"
        )
        teaching_service.add_material(
            world["admin"], world["course"], title="MATTITLE", url="https://example.com"
        )

        assert "HWTEXT" in self._body(
            app, world["admin"], f"/assistant/courses/{course_id}/homework"
        )
        assert "FBTEXT" in self._body(
            app, world["admin"], f"/assistant/courses/{course_id}/feedback"
        )
        assert "MATTITLE" in self._body(
            app, world["admin"], f"/assistant/courses/{course_id}/materials"
        )

    def test_teacher_pages_render(self, app, world):
        course_id = world["course"].id
        teaching_service.add_material(
            world["teacher"], world["course"], title="MATTITLE", url="https://example.com"
        )
        assert "MATTITLE" in self._body(
            app, world["teacher"], f"/teacher/courses/{course_id}/materials"
        )
        assert "MATTITLE" in self._body(
            app, world["teacher"], f"/teacher/courses/{course_id}"
        )


class TestDeletion:
    def test_removing_homework_is_audited(self, app, db, world):
        item = teaching_service.add_homework(world["admin"], world["course"], text="x")
        item_id = item.id
        teaching_service.delete_homework(world["admin"], item)
        entry = db.session.scalar(
            select(AuditLog).where(
                AuditLog.action == "homework.delete", AuditLog.entity_id == str(item_id)
            )
        )
        assert entry is not None
        assert entry.before_json["text"] == "x"

    def test_archiving_a_course_hides_its_teaching_content(self, app, world):
        """Scoped through `courses_for`, which excludes archived courses."""
        teaching_service.add_homework(world["admin"], world["course"], text="x")
        course_service.archive_course(world["admin"], world["course"])
        assert homework_for(world["student_a"]) == []


class TestDateFiltering:
    def test_homework_comes_back_newest_first(self, app, world):
        today = date.today()
        for offset in (2, 0, 1):
            teaching_service.add_homework(
                world["admin"],
                world["course"],
                text=f"day-{offset}",
                for_date=today - timedelta(days=offset),
            )
        dates = [h.for_date for h in homework_for(world["admin"])]
        assert dates == sorted(dates, reverse=True)
