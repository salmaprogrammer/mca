"""Sprint S1.7 — the authorization wall.

This file grows every phase and becomes the full role x route matrix in S9.2.
The brief's warning is the thing being defended: a teacher's session must not
be able to fetch another teacher's data by guessing an ID.

Two denial codes, deliberately:
  * 403 for a whole role-gated area — those URLs are fixed and public, so
    hiding their existence buys nothing.
  * 404 for an out-of-scope row — there the ID itself is the secret, and a 403
    would confirm it exists.
"""

from __future__ import annotations

import pytest

from app.models.enums import Role
from tests.conftest import link_parent, login, make_user

# url -> roles allowed
ROUTE_MATRIX = {
    "/admin/": {Role.ADMIN},
    "/admin/assistants": {Role.ADMIN},
    "/admin/audit": {Role.ADMIN},
    "/assistant/": {Role.ADMIN, Role.ASSISTANT},
    "/assistant/people/teachers": {Role.ADMIN, Role.ASSISTANT},
    "/assistant/people/students": {Role.ADMIN, Role.ASSISTANT},
    "/assistant/courses": {Role.ADMIN, Role.ASSISTANT},
    "/assistant/courses/new": {Role.ADMIN, Role.ASSISTANT},
    "/assistant/sessions": {Role.ADMIN, Role.ASSISTANT},
    "/assistant/attendance": {Role.ADMIN, Role.ASSISTANT},
    "/assistant/payments": {Role.ADMIN, Role.ASSISTANT},
    "/assistant/whatsapp": {Role.ADMIN, Role.ASSISTANT},
    "/portal/attendance": {Role.STUDENT, Role.PARENT},
    "/portal/messages": {Role.STUDENT, Role.PARENT},
    "/teacher/": {Role.TEACHER},
    "/portal/": {Role.STUDENT, Role.PARENT},
}

ALL_ROLES = [Role.ADMIN, Role.ASSISTANT, Role.TEACHER, Role.STUDENT, Role.PARENT]


@pytest.fixture
def clients(app, seeded_course_types, admin, assistant, teacher, student, parent):
    """One logged-in client per role.

    Course types are seeded so the course pages render their real content
    rather than the "not set up yet" redirect.
    """
    people = {
        Role.ADMIN: admin,
        Role.ASSISTANT: assistant,
        Role.TEACHER: teacher,
        Role.STUDENT: student,
        Role.PARENT: parent,
    }
    made = {}
    for role, user in people.items():
        c = app.test_client()
        login(c, user)
        made[role] = c
    return made


ROUTE_CASES = sorted(
    (url, tuple(sorted(r.value for r in allowed))) for url, allowed in ROUTE_MATRIX.items()
)


@pytest.mark.parametrize("url,allowed", ROUTE_CASES)
@pytest.mark.parametrize("role", ALL_ROLES)
def test_route_matrix(clients, url, allowed, role):
    response = clients[role].get(url, follow_redirects=False)
    if role.value in allowed:
        assert response.status_code == 200, f"{role.value} should reach {url}"
    else:
        assert response.status_code == 403, f"{role.value} must not reach {url}"


@pytest.mark.parametrize("url", sorted(ROUTE_MATRIX))
def test_anonymous_is_sent_to_login(client, url):
    response = client.get(url, follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


class TestRowScoping:
    def test_a_parent_cannot_read_another_familys_child(self, app, db, parent, seeded_terms):
        """404, not 403 — a 403 would confirm the student ID exists."""
        other_student = make_user(Role.STUDENT, phone="+201033330000", name="Someone Else")
        other_parent = make_user(Role.PARENT, phone="+201033331111")
        link_parent(other_parent, other_student)

        client = app.test_client()
        login(client, parent)

        assert client.get(f"/portal/students/{other_student.id}").status_code == 404
        assert (
            client.post(
                f"/portal/child/{other_student.id}/select", follow_redirects=False
            ).status_code
            == 404
        )

    def test_a_parent_can_read_their_own_child(self, app, parent, student):
        client = app.test_client()
        login(client, parent)
        assert client.get(f"/portal/students/{student.id}").status_code == 200

    def test_a_teacher_sees_no_students_before_enrolments_exist(self, app, teacher, student):
        """Teachers resolve students through their own courses only.

        There is no code path from a teacher to the global student list, which
        is why this is empty rather than "all students" (extended in S2.6).
        """
        from app.services.scoping import students_for

        assert students_for(teacher) == []

    def test_an_assistant_cannot_touch_the_admin_only_account_area(self, app, assistant, admin):
        client = app.test_client()
        login(client, assistant)
        assert client.get("/admin/assistants").status_code == 403
        assert (
            client.post(
                f"/admin/users/{admin.id}/regenerate-password", follow_redirects=False
            ).status_code
            == 403
        )

    def test_a_teacher_cannot_regenerate_anyones_password(self, app, teacher, student):
        client = app.test_client()
        login(client, teacher)
        response = client.post(
            f"/assistant/people/{student.id}/regenerate-password", follow_redirects=False
        )
        assert response.status_code == 403


class TestSelfService:
    def test_a_user_cannot_deactivate_themselves(self, app, db, admin):
        client = app.test_client()
        login(client, admin)
        client.post(f"/admin/users/{admin.id}/deactivate", follow_redirects=False)
        db.session.refresh(admin)
        assert admin.is_active is True

    def test_students_and_parents_can_change_their_own_password(self, app, student):
        client = app.test_client()
        login(client, student)
        assert client.get("/password/change").status_code == 200
