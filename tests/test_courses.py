"""Sprints S2.1, S2.2, S2.3, S2.6 — catalogue, slots, and role scoping."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.course import CourseType
from app.models.enums import CourseStatus, Role
from app.services import courses as course_service
from app.services import enrollments as enrollment_service
from app.services.scoping import courses_for, courses_for_student
from tests.conftest import link_parent, login, make_course, make_user

SUNDAY = 6
WEDNESDAY = 2
MONDAY = 0


class TestCourseTypes:
    def test_all_six_are_seeded(self, app, seeded_course_types):
        assert len(seeded_course_types) == 6

    def test_seeding_twice_does_not_duplicate(self, app, db, seeded_course_types):
        course_service.seed_course_types()
        assert db.session.scalar(select(db.func.count(CourseType.id))) == 6

    def test_type_six_meets_twice_a_week(self, app, seeded_course_types):
        """Open question 2, answered: 14 sessions over 7 weeks."""
        advanced = seeded_course_types["advanced"]
        assert advanced.sessions_per_week == 2
        assert advanced.total_sessions == 14
        assert advanced.cycle.value == "round"

    def test_there_is_no_route_that_edits_a_type(self, app):
        """"Types 1–6 are not freely editable" is enforced by the absence of
        any endpoint that writes to the table — not by a UI check."""
        writable = [
            rule
            for rule in app.url_map.iter_rules()
            if "course_type" in rule.rule.lower()
            and {"POST", "PATCH", "PUT", "DELETE"} & rule.methods
        ]
        assert writable == []


class TestDisplayTitle:
    def test_the_type_leads_for_subject_types(
        self, app, db, seeded_terms, seeded_course_types, admin
    ):
        """Open question 3: the type is the product a student sees."""
        teacher = make_user(Role.TEACHER, phone="+201011118001")
        course = make_course(
            admin, teacher=teacher, name="Nov round — Mr Ahmed", course_type_code="sat_basics"
        )
        assert course.display_title("en") == "SAT Basics"
        assert course.display_title("ar") == "أساسيات SAT"

    def test_the_package_type_falls_back_to_the_course_name(
        self, app, db, seeded_terms, seeded_course_types, admin
    ):
        """Type 1's label is a pricing package with no subject in it, so a
        course of that type has to show its own name instead."""
        teacher = make_user(Role.TEACHER, phone="+201011118002")
        course = make_course(
            admin,
            teacher=teacher,
            name="Chemistry with Ms Sara",
            course_type_code="monthly_4",
            slots=[{"weekday": MONDAY, "start_time": "15:00"}],
        )
        assert course.course_type.has_own_subject is False
        assert course.display_title("en") == "Chemistry with Ms Sara"


class TestSlotCount:
    def test_slot_count_must_match_the_type(
        self, app, db, seeded_terms, seeded_course_types, admin
    ):
        teacher = make_user(Role.TEACHER, phone="+201011118010")
        with pytest.raises(course_service.CourseError) as exc:
            make_course(
                admin,
                teacher=teacher,
                course_type_code="gpa_course",  # needs 2/week
                slots=[{"weekday": SUNDAY, "start_time": "16:00"}],
            )
        assert "2" in str(exc.value)

    def test_a_two_per_week_type_stores_two_slots(
        self, app, db, seeded_terms, seeded_course_types, admin
    ):
        teacher = make_user(Role.TEACHER, phone="+201011118011")
        course = make_course(admin, teacher=teacher, course_type_code="gpa_course")
        assert len(course.slots) == 2

    def test_slots_default_to_ninety_minutes(
        self, app, db, seeded_terms, seeded_course_types, admin
    ):
        """Open question 4, answered."""
        teacher = make_user(Role.TEACHER, phone="+201011118012")
        course = make_course(admin, teacher=teacher, course_type_code="gpa_course")
        assert {s.duration_minutes for s in course.slots} == {90}


class TestCourseLifecycle:
    def test_creation_is_audited(self, app, db, seeded_terms, seeded_course_types, admin):
        teacher = make_user(Role.TEACHER, phone="+201011118020")
        course = make_course(admin, teacher=teacher)
        entry = db.session.scalar(
            select(AuditLog).where(
                AuditLog.action == "course.create", AuditLog.entity_id == str(course.id)
            )
        )
        assert entry is not None
        assert entry.actor_id == admin.id

    def test_archiving_hides_it_from_the_active_list(
        self, app, db, seeded_terms, seeded_course_types, admin
    ):
        teacher = make_user(Role.TEACHER, phone="+201011118021")
        course = make_course(admin, teacher=teacher)
        course_service.archive_course(admin, course)

        assert course.status is CourseStatus.ARCHIVED
        assert course.id not in {c.id for c in courses_for(admin)}
        assert course.id in {c.id for c in courses_for(admin, include_archived=True)}

    def test_a_course_needs_a_real_teacher(
        self, app, db, seeded_terms, seeded_course_types, admin, student
    ):
        with pytest.raises(course_service.CourseError):
            course_service.create_course(
                admin,
                name="Bad",
                course_type_id=1,
                teacher_id=student.id,  # not a teacher
                slots=[{"weekday": MONDAY, "start_time": "16:00"}],
            )


class TestScoping:
    """Sprint S2.6 — the wall the brief cares most about."""

    @pytest.fixture
    def world(self, db, seeded_terms, seeded_course_types, admin):
        teacher_a = make_user(Role.TEACHER, phone="+201011118040", name="Ahmed")
        teacher_b = make_user(Role.TEACHER, phone="+201011118041", name="Sara")

        course_a = make_course(
            admin, teacher=teacher_a, name="A course", course_type_code="sat_basics"
        )
        course_b = make_course(
            admin,
            teacher=teacher_b,
            name="B course",
            course_type_code="est_basics",
            slots=[
                {"weekday": MONDAY, "start_time": "10:00"},
                {"weekday": WEDNESDAY, "start_time": "10:00"},
            ],
        )

        student_a = make_user(Role.STUDENT, phone="+201055558040", name="Student A")
        student_b = make_user(Role.STUDENT, phone="+201055558041", name="Student B")
        enrollment_service.enroll(admin, course_a, student_a)
        enrollment_service.enroll(admin, course_b, student_b)

        parent_a = make_user(Role.PARENT, phone="+201099998040", name="Parent A")
        link_parent(parent_a, student_a)

        return locals()

    def test_a_teacher_sees_only_their_own_courses(self, app, world):
        assert {c.name for c in courses_for(world["teacher_a"])} == {"A course"}
        assert {c.name for c in courses_for(world["teacher_b"])} == {"B course"}

    def test_a_student_sees_only_courses_they_are_enrolled_in(self, app, world):
        assert {c.name for c in courses_for(world["student_a"])} == {"A course"}

    def test_a_parent_sees_only_their_childs_courses(self, app, world):
        assert {c.name for c in courses_for(world["parent_a"])} == {"A course"}

    def test_staff_see_everything(self, app, world):
        assert {c.name for c in courses_for(world["admin"])} == {"A course", "B course"}

    def test_a_teacher_only_sees_students_in_their_own_courses(self, app, world):
        from app.services.scoping import students_for

        assert {s.full_name for s in students_for(world["teacher_a"])} == {"Student A"}
        assert {s.full_name for s in students_for(world["teacher_b"])} == {"Student B"}

    def test_a_parent_cannot_pull_another_familys_child_courses(self, app, world):
        from werkzeug.exceptions import NotFound

        with pytest.raises(NotFound):
            courses_for_student(world["parent_a"], world["student_b"])


class TestRouteScoping:
    """Same wall, exercised over HTTP with real sessions."""

    @pytest.fixture
    def world(self, app, db, seeded_terms, seeded_course_types, admin):
        from tests.conftest import accept_current_terms

        teacher_a = make_user(Role.TEACHER, phone="+201011118050", name="Ahmed")
        teacher_b = make_user(Role.TEACHER, phone="+201011118051", name="Sara")
        for t in (teacher_a, teacher_b):
            accept_current_terms(t)

        course_a = make_course(admin, teacher=teacher_a, name="A", course_type_code="sat_basics")
        course_b = make_course(
            admin,
            teacher=teacher_b,
            name="B",
            course_type_code="est_basics",
            slots=[
                {"weekday": MONDAY, "start_time": "10:00"},
                {"weekday": WEDNESDAY, "start_time": "10:00"},
            ],
        )
        return {"a": course_a, "b": course_b, "ta": teacher_a, "tb": teacher_b}

    def test_a_teacher_gets_404_for_another_teachers_course(self, app, world):
        client = app.test_client()
        login(client, world["ta"])
        assert client.get(f"/teacher/courses/{world['a'].id}").status_code == 200
        # 404 rather than 403: the ID itself is the secret.
        assert client.get(f"/teacher/courses/{world['b'].id}").status_code == 404

    def test_a_student_gets_404_for_a_course_they_are_not_in(
        self, app, db, world, student
    ):
        client = app.test_client()
        login(client, student)
        assert client.get(f"/portal/courses/{world['a'].id}").status_code == 404

    def test_a_student_can_open_their_own_course(self, app, db, world, admin, student):
        enrollment_service.enroll(admin, world["a"], student)
        client = app.test_client()
        login(client, student)
        assert client.get(f"/portal/courses/{world['a'].id}").status_code == 200

    def test_a_teacher_cannot_reach_the_staff_course_editor(self, app, world):
        client = app.test_client()
        login(client, world["ta"])
        assert client.get(f"/assistant/courses/{world['a'].id}/edit").status_code == 403

    def test_staff_can_open_any_course(self, app, world, admin):
        client = app.test_client()
        login(client, admin)
        assert client.get(f"/assistant/courses/{world['b'].id}").status_code == 200


class TestRendering:
    """Every course page, rendered with real data in it.

    Added after a template bug reached the browser despite a green suite: the
    shared course-card macro is imported with `{% from ... import %}`, which
    gives it no template context, so a `context_processor` value it used was
    silently undefined. No test had rendered a course *list* with a course in
    it, so the macro body never ran. These fetch each page with data present.
    """

    @pytest.fixture
    def world(self, app, db, seeded_terms, seeded_course_types, admin):
        from tests.conftest import accept_current_terms

        teacher = make_user(Role.TEACHER, phone="+201011118060", name="Ahmed Fathy")
        accept_current_terms(teacher)
        student = make_user(Role.STUDENT, phone="+201055558060", name="Youssef")
        accept_current_terms(student)
        parent = make_user(Role.PARENT, phone="+201099998060", name="Adel")
        link_parent(parent, student)
        accept_current_terms(parent)

        course = make_course(
            admin, teacher=teacher, name="Nov round", course_type_code="gpa_course"
        )
        enrollment_service.enroll(admin, course, student)
        return {
            "course": course,
            "teacher": teacher,
            "student": student,
            "parent": parent,
            "admin": admin,
        }

    def _get(self, app, user, url):
        client = app.test_client()
        login(client, user)
        response = client.get(url)
        assert response.status_code == 200, f"{url} returned {response.status_code}"
        return response.get_data(as_text=True)

    def test_staff_course_list_renders_the_card(self, app, world):
        body = self._get(app, world["admin"], "/assistant/courses")
        assert "Nov round" in body
        assert "16:00" in body  # the slot label

    def test_staff_course_detail_renders(self, app, world):
        body = self._get(app, world["admin"], f"/assistant/courses/{world['course'].id}")
        assert "Youssef" in body  # the enrolled student

    def test_staff_course_form_renders(self, app, world):
        body = self._get(app, world["admin"], "/assistant/courses/new")
        assert "SAT Basics" in body  # a seeded type in the dropdown

    def test_staff_course_edit_renders(self, app, world):
        body = self._get(app, world["admin"], f"/assistant/courses/{world['course'].id}/edit")
        assert "Nov round" in body

    def test_teacher_dashboard_renders_courses_and_calendar(self, app, world):
        body = self._get(app, world["teacher"], "/teacher/")
        assert "GPA" in body or "كورس" in body
        assert "Youssef" in body  # their student, via enrolment

    def test_teacher_course_detail_renders(self, app, world):
        body = self._get(app, world["teacher"], f"/teacher/courses/{world['course'].id}")
        assert "Youssef" in body

    def test_student_portal_renders_their_course(self, app, world):
        body = self._get(app, world["student"], "/portal/")
        assert "16:00" in body

    def test_parent_portal_renders_their_childs_course(self, app, world):
        body = self._get(app, world["parent"], "/portal/")
        assert "16:00" in body

    def test_portal_course_detail_renders(self, app, world):
        body = self._get(app, world["student"], f"/portal/courses/{world['course'].id}")
        assert "Ahmed Fathy" in body

    def test_pages_render_in_english_too(self, app, world):
        """Macros pick up the locale; a context-less macro breaks here first."""
        client = app.test_client()
        login(client, world["admin"])
        client.post("/me/language", data={"locale": "en", "next": "/assistant/courses"})
        body = client.get("/assistant/courses").get_data(as_text=True)
        assert "GPA Course" in body
        assert "Sunday" in body
