"""Sprint S1.6 — account creation, family linking, credential lifecycle."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.enums import Role
from app.models.user import ParentLink, User
from app.services import accounts as accounts_service
from app.services.auth import PhoneNumberError, authenticate
from tests.conftest import login, make_user


class TestAssistantCreation:
    def test_admin_creates_an_assistant_with_a_working_login(self, app, db, admin):
        result = accounts_service.create_assistant(admin, "Mona", "01000000055")
        account = result.accounts[0]

        assert account.user.role is Role.ASSISTANT
        assert account.user.username == "+201000000055"
        assert account.plaintext_password
        assert authenticate("01000000055", account.plaintext_password) is not None

    def test_new_accounts_must_change_their_password(self, app, db, admin):
        result = accounts_service.create_assistant(admin, "Mona", "01000000056")
        assert result.accounts[0].user.must_change_password is True

    def test_duplicate_phone_is_refused(self, app, db, admin):
        accounts_service.create_assistant(admin, "Mona", "01000000057")
        with pytest.raises(accounts_service.AccountError):
            accounts_service.create_assistant(admin, "Someone Else", "01000000057")

    def test_invalid_phone_is_refused(self, app, db, admin):
        with pytest.raises(PhoneNumberError):
            accounts_service.create_assistant(admin, "Mona", "nonsense")


class TestTeacherCreation:
    def test_teacher_gets_a_profile_and_no_parent(self, app, db, assistant):
        result = accounts_service.create_teacher(assistant, "Ahmed", "01011113333", "Physics")
        teacher = result.accounts[0].user

        assert teacher.role is Role.TEACHER
        assert teacher.teacher_profile.subject == "Physics"
        assert len(result.accounts) == 1


class TestFamilyCreation:
    def test_distinct_phones_create_two_logins(self, app, db, assistant):
        result = accounts_service.create_student_with_parent(
            assistant,
            student_name="Youssef",
            student_phone="01055551111",
            parent_name="Adel",
            parent_phone="01099991111",
        )
        by_label = {a.label: a for a in result.accounts}

        assert by_label["Student"].has_login
        assert by_label["Parent"].has_login
        assert by_label["Student"].user.username == "+201055551111"
        assert by_label["Parent"].user.username == "+201099991111"

    def test_one_phone_gives_the_parent_the_login_not_the_student(self, app, db, assistant):
        """OPEN QUESTION 1, recommendation (b).

        Username is the phone number and phones are unique, so a shared number
        cannot produce two logins. The parent gets it; the student is still a
        full record, reachable through the parent's account.
        """
        result = accounts_service.create_student_with_parent(
            assistant,
            student_name="Nour",
            student_phone="01077778888",
            parent_name="Hassan",
            parent_phone="01077778888",
        )
        by_label = {a.label: a for a in result.accounts}

        assert by_label["Parent"].has_login
        assert by_label["Parent"].user.username == "+201077778888"
        assert not by_label["Student"].has_login
        assert by_label["Student"].user.username is None
        assert by_label["Student"].user.role is Role.STUDENT
        assert result.notices  # the operator is told why

    def test_student_phone_alone_is_treated_as_the_family_number(self, app, db, assistant):
        result = accounts_service.create_student_with_parent(
            assistant, student_name="Sara", student_phone="01066667777"
        )
        by_label = {a.label: a for a in result.accounts}
        assert by_label["Parent"].user.username == "+201066667777"
        assert not by_label["Student"].has_login

    def test_existing_parent_is_linked_not_duplicated(self, app, db, assistant):
        """One parent, several children — the brief requires this explicitly."""
        first = accounts_service.create_student_with_parent(
            assistant,
            student_name="Child One",
            student_phone="01055552222",
            parent_name="Shared Parent",
            parent_phone="01099992222",
        )
        parent_id = {a.label: a for a in first.accounts}["Parent"].user.id

        second = accounts_service.create_student_with_parent(
            assistant,
            student_name="Child Two",
            student_phone="01055553333",
            parent_phone="01099992222",
        )

        parents = db.session.scalars(select(User).where(User.role == Role.PARENT)).all()
        assert len(parents) == 1

        links = db.session.scalars(
            select(ParentLink).where(ParentLink.parent_id == parent_id)
        ).all()
        assert len(links) == 2
        # No second parent account was created, so nothing to hand over.
        assert "Parent" not in {a.label for a in second.accounts}
        assert second.notices

    def test_phone_belonging_to_a_teacher_is_refused_with_a_clear_message(
        self, app, db, assistant
    ):
        accounts_service.create_teacher(assistant, "Ahmed", "01011114444")

        with pytest.raises(accounts_service.AccountError) as exc:
            accounts_service.create_student_with_parent(
                assistant,
                student_name="Someone",
                student_phone="01055554444",
                parent_phone="01011114444",
            )
        message = str(exc.value)
        assert "teacher" in message
        assert "Ahmed" in message

    def test_student_phone_already_taken_is_refused(self, app, db, assistant):
        accounts_service.create_teacher(assistant, "Ahmed", "01011115555")
        with pytest.raises(accounts_service.AccountError):
            accounts_service.create_student_with_parent(
                assistant,
                student_name="Someone",
                student_phone="01011115555",
                parent_phone="01099995555",
            )

    def test_no_phone_at_all_is_refused(self, app, db, assistant):
        with pytest.raises(accounts_service.AccountError):
            accounts_service.create_student_with_parent(assistant, student_name="Ghost")

    def test_same_name_under_same_parent_is_refused(self, app, db, assistant):
        """Guards the "created twice" bug: rapid double-submit or a browser
        refresh would otherwise silently duplicate a family student, since a
        shared-phone student has no phone to collide on.
        """
        accounts_service.create_student_with_parent(
            assistant,
            student_name="Omar Wael",
            parent_name="Wael",
            parent_phone="01055550001",
        )
        with pytest.raises(accounts_service.AccountError) as exc:
            accounts_service.create_student_with_parent(
                assistant,
                student_name="  omar   wael  ",  # whitespace + case must still match
                parent_phone="01055550001",
            )
        assert "Omar" in str(exc.value) or "omar" in str(exc.value)

    def test_a_second_child_with_a_distinct_name_still_works(self, app, db, assistant):
        """The duplicate guard must not block real siblings."""
        accounts_service.create_student_with_parent(
            assistant,
            student_name="Omar Wael",
            parent_name="Wael",
            parent_phone="01055550002",
        )
        result = accounts_service.create_student_with_parent(
            assistant,
            student_name="Youssef Wael",
            parent_phone="01055550002",
        )
        by_label = {a.label: a for a in result.accounts}
        assert by_label["Student"].user.full_name == "Youssef Wael"


class TestCredentialLifecycle:
    def test_regenerating_replaces_the_old_password(self, app, db, admin):
        result = accounts_service.create_assistant(admin, "Mona", "01000000077")
        user = result.accounts[0].user
        old = result.accounts[0].plaintext_password

        new = accounts_service.regenerate_password(admin, user)

        assert new != old
        assert authenticate(user.username, old) is None
        assert authenticate(user.username, new) is not None
        assert user.must_change_password is True

    def test_cannot_regenerate_for_an_account_with_no_login(self, app, db, assistant):
        result = accounts_service.create_student_with_parent(
            assistant, student_name="Nour", parent_phone="01077779999"
        )
        student = {a.label: a for a in result.accounts}["Student"].user

        with pytest.raises(accounts_service.AccountError):
            accounts_service.regenerate_password(assistant, student)

    def test_deactivation_blocks_login(self, app, db, admin):
        result = accounts_service.create_assistant(admin, "Mona", "01000000078")
        user = result.accounts[0].user
        password = result.accounts[0].plaintext_password

        accounts_service.set_active(admin, user, False)

        assert authenticate(user.username, password) is None


class TestCreationRouteIsPRG:
    """Post-Redirect-Get is the real fix for the double-submit duplicate bug:
    a refresh on the success page must not replay the POST.
    """

    def test_student_post_redirects_and_credentials_show_after_redirect(
        self, app, db, admin
    ):
        client = app.test_client()
        login(client, admin)

        response = client.post(
            "/assistant/people/students",
            data={
                "student_name": "Omar Wael",
                "student_phone": "",
                "parent_name": "Wael",
                "parent_phone": "01055550100",
                "school": "",
                "grade": "",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/assistant/people/students" in response.headers["Location"]

        follow = client.get(response.headers["Location"])
        assert follow.status_code == 200
        body = follow.get_data(as_text=True)
        assert "Omar Wael" in body

    def test_refreshing_the_success_page_does_not_create_a_duplicate(
        self, app, db, admin
    ):
        client = app.test_client()
        login(client, admin)

        client.post(
            "/assistant/people/students",
            data={
                "student_name": "Omar Wael",
                "student_phone": "",
                "parent_name": "Wael",
                "parent_phone": "01055550101",
                "school": "",
                "grade": "",
            },
            follow_redirects=True,
        )
        client.get("/assistant/people/students")  # simulate F5

        omars = db.session.scalars(
            _select_users_named("Omar Wael")
        ).all()
        assert len(omars) == 1

    def test_teacher_post_redirects_too(self, app, db, admin):
        client = app.test_client()
        login(client, admin)

        response = client.post(
            "/assistant/people/teachers",
            data={
                "full_name": "Ahmed Fathy",
                "phone": "01055550102",
                "subject": "Physics",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/assistant/people/teachers" in response.headers["Location"]


def _select_users_named(name: str):
    """Small helper for the PRG tests above."""
    from sqlalchemy import select as _select

    return _select(User).where(User.full_name == name)


class TestProfileEditing:
    def test_updating_a_students_name_and_school(self, app, db, admin):
        result = accounts_service.create_student_with_parent(
            admin,
            student_name="Omar Wael",
            parent_name="Wael",
            parent_phone="01055551201",
            school="Nasr City School",
        )
        student = {a.label: a for a in result.accounts}["Student"].user

        accounts_service.update_user_profile(
            admin,
            student,
            full_name="Omar Ahmed Wael",
            school="Nozha School",
        )
        db.session.refresh(student)
        assert student.full_name == "Omar Ahmed Wael"
        assert student.student_profile.school == "Nozha School"

    def test_taking_a_username_that_belongs_to_another_account_is_refused(
        self, app, db, admin
    ):
        first = accounts_service.create_assistant(admin, "Mona", "01055551301")
        accounts_service.update_user_profile(
            admin, first.accounts[0].user, username="mona.a"
        )
        second = accounts_service.create_assistant(admin, "Mariam", "01055551302")
        with pytest.raises(accounts_service.AccountError) as exc:
            accounts_service.update_user_profile(
                admin, second.accounts[0].user, username="mona.a"
            )
        assert "already taken" in str(exc.value).lower() or "taken" in str(exc.value).lower()

    def test_a_username_lets_the_person_sign_in_with_it(self, app, db, admin):
        result = accounts_service.create_assistant(admin, "Mona", "01055551401")
        user = result.accounts[0].user
        password = result.accounts[0].plaintext_password

        accounts_service.update_user_profile(admin, user, username="mona.a")
        assert authenticate("mona.a", password) is not None
        assert authenticate("MONA.A", password) is not None  # case-insensitive
        assert authenticate("01055551401", password) is not None  # phone still works


class TestSafeDelete:
    def test_a_fresh_duplicate_student_is_hard_deleted(self, app, db, admin):
        result = accounts_service.create_student_with_parent(
            admin,
            student_name="Ghost Twin",
            parent_name="Family",
            parent_phone="01055551501",
        )
        student = {a.label: a for a in result.accounts}["Student"].user
        student_id = student.id

        outcome = accounts_service.delete_user_safely(admin, student)
        assert outcome == "deleted"
        assert db.session.get(User, student_id) is None

    def test_a_student_with_history_is_deactivated_not_deleted(
        self, app, db, admin, seeded_terms, seeded_course_types
    ):
        from tests.conftest import make_course, make_user
        from app.services import enrollments as enrollment_service

        teacher = make_user(Role.TEACHER, phone="+201055551601")
        course = make_course(admin, teacher=teacher, name="Nov")
        student = make_user(Role.STUDENT, phone="+201055551602")
        enrollment_service.enroll(admin, course, student)

        outcome = accounts_service.delete_user_safely(admin, student)
        assert outcome == "deactivated"
        db.session.refresh(student)
        assert student.id is not None
        assert student.is_active is False

    def test_deleting_your_own_account_is_refused(self, app, db, admin):
        with pytest.raises(accounts_service.AccountError):
            accounts_service.delete_user_safely(admin, admin)

    def test_delete_records_who_deleted_and_survives_the_user_row(
        self, app, db, admin
    ):
        result = accounts_service.create_student_with_parent(
            admin,
            student_name="To Delete",
            parent_name="Parent",
            parent_phone="01055551701",
        )
        student = {a.label: a for a in result.accounts}["Student"].user
        accounts_service.delete_user_safely(admin, student)

        entry = db.session.scalar(
            _select_audit_by_action_entity("account.deleted", student.id)
        )
        assert entry is not None
        assert entry.actor_id == admin.id
        assert entry.before_json["full_name"] == "To Delete"


def _select_audit_by_action_entity(action: str, entity_id: int):
    from sqlalchemy import select as _select
    from app.models.audit import AuditLog

    return (
        _select(AuditLog)
        .where(AuditLog.action == action)
        .where(AuditLog.entity_id == str(entity_id))
    )


class TestAuditing:
    def test_creation_is_attributed_to_the_actor(self, app, db, admin):
        result = accounts_service.create_assistant(admin, "Mona", "01000000079")
        entry = db.session.scalar(
            select(AuditLog)
            .where(AuditLog.action == "account.create")
            .where(AuditLog.entity_id == str(result.accounts[0].user.id))
        )
        assert entry is not None
        assert entry.actor_id == admin.id

    def test_audit_payloads_never_contain_a_password(self, app, db, admin):
        accounts_service.create_assistant(admin, "Mona", "01000000080")
        for entry in db.session.scalars(select(AuditLog)).all():
            blob = f"{entry.before_json}{entry.after_json}"
            assert "password_hash" not in blob
            assert "argon2" not in blob

    def test_password_regeneration_is_audited(self, app, db, admin):
        user = make_user(Role.TEACHER, phone="+201011119999")
        accounts_service.regenerate_password(admin, user)
        entry = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "account.password_regenerated")
        )
        assert entry is not None
        assert entry.actor_id == admin.id
