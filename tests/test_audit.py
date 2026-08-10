"""Sprints S8.1–S8.4 — the audit trail.

The point of this phase is that attendance and money changes are attributable
to a named actor with a timestamp. The load-bearing test is
`test_every_mutating_route_is_classified`: it fails when someone adds a
mutating route without deciding whether it needs auditing, which is what stops
coverage rotting quietly after handoff.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.enums import AttendanceStatus, PaymentStatus, Role
from app.services import attendance as attendance_service
from app.services import audit as audit_service
from app.services import enrollments as enrollment_service
from app.services import sessions as session_service
from app.services import teaching as teaching_service
from tests.conftest import accept_current_terms, link_parent, login, make_course, make_user

SUNDAY = 6
WEDNESDAY = 2
START = date(2026, 9, 6)


# ---------------------------------------------------------------------------
# S8.3 — the coverage registry.
#
# Every mutating route must appear here exactly once, mapped either to the
# audit action it produces or to NO_AUDIT with a stated reason. Adding a route
# without classifying it fails the test below, forcing a decision rather than
# a silent gap.
# ---------------------------------------------------------------------------

NO_AUDIT = "no-audit"

ROUTE_AUDIT_REGISTRY: dict[str, tuple[str, str]] = {
    # --- authentication -----------------------------------------------
    "auth.login": ("auth.login", "records who signed in, and failures"),
    "auth.logout": ("auth.logout", ""),
    "auth.change_password": ("account.password_changed", ""),
    "auth.terms": ("terms.accepted", "who accepted which version, when"),
    # --- accounts -----------------------------------------------------
    "admin.assistants": ("account.create", ""),
    "admin.regenerate_password": ("account.password_regenerated", ""),
    "admin.deactivate": ("account.deactivated", ""),
    "assistant.teachers": ("account.create", ""),
    "assistant.students": ("account.create", ""),
    "assistant.regenerate_password": ("account.password_regenerated", ""),
    # --- courses ------------------------------------------------------
    "assistant.course_new": ("course.create", ""),
    "assistant.course_edit": ("course.update", ""),
    "assistant.course_archive": ("course.archive", ""),
    "assistant.course_enroll": ("enrollment.create", ""),
    "assistant.enrollment_remove": ("enrollment.delete", ""),
    # --- sessions and attendance -------------------------------------
    "assistant.generate_sessions": ("session.generate", ""),
    "assistant.mark_student": ("attendance.mark", "the brief's core requirement"),
    "assistant.mark_teacher": ("attendance.teacher_mark", ""),
    "assistant.cancel_session": ("session.cancel", "who cancelled drives makeup rules"),
    "assistant.reschedule_session": ("session.reschedule", ""),
    "teacher.mark_student": ("attendance.mark", ""),
    # --- teaching content ---------------------------------------------
    "assistant.course_homework": ("homework.create", ""),
    "assistant.homework_delete": ("homework.delete", ""),
    "assistant.course_feedback": ("feedback.create", ""),
    "assistant.feedback_delete": ("feedback.delete", ""),
    "assistant.course_materials": ("material.create", ""),
    "assistant.material_delete": ("material.delete", ""),
    "teacher.course_materials": ("material.create", ""),
    "teacher.material_delete": ("material.delete", ""),
    # --- money ---------------------------------------------------------
    "assistant.enrollment_booking": ("enrollment.booking_changed", ""),
    "assistant.enrollment_payment": ("enrollment.payment_changed", ""),
    "assistant.enrollment_amount": ("enrollment.amount_changed", ""),
    # --- messaging ------------------------------------------------------
    "assistant.whatsapp_open": ("whatsapp.send", "who took a message to WhatsApp"),
    "assistant.whatsapp_prepare_all": ("whatsapp.prepare", ""),
    # --- deliberately not audited ---------------------------------------
    "main.set_language": (
        NO_AUDIT,
        "writes a display preference to the session cookie; no centre data changes",
    ),
    "portal.select_child": (
        NO_AUDIT,
        "changes which of the parent's own children they are looking at; reads only",
    ),
    "assistant.course_slot_rows": (NO_AUDIT, "HTMX partial re-render; writes nothing"),
    "assistant.course_check_conflicts": (NO_AUDIT, "read-only conflict preview"),
}

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@pytest.fixture
def world(app, db, seeded_terms, seeded_course_types, admin):
    teacher = make_user(Role.TEACHER, phone="+201011119901", name="Ahmed")
    accept_current_terms(teacher)
    student = make_user(Role.STUDENT, phone="+201055559901", name="Youssef")
    accept_current_terms(student)
    parent = make_user(Role.PARENT, phone="+201099999901", name="Adel")
    link_parent(parent, student)
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
    enrollment_service.enroll(admin, course, student)
    session_service.generate_sessions(admin, course)
    return {
        "admin": admin,
        "teacher": teacher,
        "student": student,
        "parent": parent,
        "course": course,
    }


class TestCoverageRegistry:
    """S8.3 — the test that stops coverage rotting."""

    def test_every_mutating_route_is_classified(self, app):
        """A new POST route must be classified before this passes again.

        Deliberately not "every route writes an audit row": some genuinely
        should not (a language toggle changes no centre data). What must not
        happen is a route slipping in with nobody having thought about it.
        """
        mutating = {
            rule.endpoint
            for rule in app.url_map.iter_rules()
            if MUTATING_METHODS & rule.methods and rule.endpoint != "static"
        }
        unclassified = sorted(mutating - set(ROUTE_AUDIT_REGISTRY))
        assert not unclassified, (
            "These mutating routes are not in ROUTE_AUDIT_REGISTRY. Add each one "
            "with the audit action it produces, or NO_AUDIT plus a reason:\n  "
            + "\n  ".join(unclassified)
        )

    def test_the_registry_has_no_stale_entries(self, app):
        """Removing a route should remove its registry line too."""
        mutating = {
            rule.endpoint
            for rule in app.url_map.iter_rules()
            if MUTATING_METHODS & rule.methods and rule.endpoint != "static"
        }
        stale = sorted(set(ROUTE_AUDIT_REGISTRY) - mutating)
        assert not stale, f"Registry lists routes that no longer exist: {stale}"

    def test_every_no_audit_entry_states_a_reason(self):
        """"Not audited" is a decision, so it has to be justified in writing."""
        missing = [
            endpoint
            for endpoint, (action, reason) in ROUTE_AUDIT_REGISTRY.items()
            if action == NO_AUDIT and not reason.strip()
        ]
        assert not missing, f"NO_AUDIT without a reason: {missing}"

    def test_the_audited_actions_are_real(self, app, world):
        """Guards typos: a registry action nothing ever emits is worthless."""
        from app.labels import _ALL  # noqa: F401  (import proves app loads)

        emitted = set()
        for path in ("app/services", "app/blueprints"):
            from pathlib import Path

            for file in Path(path).rglob("*.py"):
                text = file.read_text(encoding="utf-8")
                import re

                emitted.update(re.findall(r'"([a-z_]+\.[a-z_]+)"', text))

        declared = {
            action for action, _reason in ROUTE_AUDIT_REGISTRY.values()
            if action != NO_AUDIT
        }
        unknown = sorted(declared - emitted)
        assert not unknown, f"Registry names actions no code emits: {unknown}"


class TestAttributability:
    """The reason the brief asked for an audit trail at all."""

    def test_attendance_records_who_marked_it(self, app, db, world):
        session = world["course"].sessions[0]
        attendance_service.mark_student(
            world["admin"], session, world["student"], AttendanceStatus.PRESENT
        )
        entry = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "attendance.mark")
        )
        assert entry.actor_id == world["admin"].id
        assert entry.created_at is not None
        assert entry.after_json["status"] == "present"

    def test_amending_attendance_keeps_the_original_value(self, app, db, world):
        """An attendance dispute needs the before, not just the after."""
        session = world["course"].sessions[0]
        attendance_service.mark_student(
            world["admin"], session, world["student"], AttendanceStatus.ABSENT
        )
        attendance_service.mark_student(
            world["admin"], session, world["student"], AttendanceStatus.PRESENT
        )
        amend = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "attendance.amend")
        )
        assert amend.before_json["status"] == "absent"
        assert amend.after_json["status"] == "present"

    def test_money_changes_are_attributable(self, app, db, world):
        enrollment = world["course"].enrollments[0]
        enrollment_service.set_payment_status(
            world["admin"], enrollment, PaymentStatus.PAID
        )
        entry = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "enrollment.payment_changed")
        )
        assert entry.actor_id == world["admin"].id
        assert entry.before_json["payment_status"] == "unpaid"
        assert entry.after_json["payment_status"] == "paid"

    def test_the_scheduled_job_is_recorded_as_having_no_actor(self, app, db, world):
        """Null actor means "the system", not "unknown person"."""
        from app.services import whatsapp as whatsapp_service

        whatsapp_service.prepare_daily_batch(None, START)
        entry = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "whatsapp.prepare")
        )
        assert entry.actor_id is None

    def test_requests_record_the_source_ip(self, app, world):
        client = app.test_client()
        login(client, world["admin"])
        client.post(
            f"/assistant/sessions/{world['course'].sessions[0].id}"
            f"/students/{world['student'].id}/mark",
            data={"status": "present"},
        )
        from app.extensions import db as _db

        entry = _db.session.scalar(
            select(AuditLog).where(AuditLog.action == "attendance.mark")
        )
        assert entry.ip is not None


class TestRedaction:
    def test_no_audit_row_ever_contains_a_password(self, app, db, world):
        from app.services import accounts as accounts_service

        accounts_service.create_assistant(world["admin"], "Mona", "01000009901")
        for entry in db.session.scalars(select(AuditLog)):
            blob = f"{entry.before_json}{entry.after_json}"
            assert "password_hash" not in blob
            assert "argon2" not in blob

    def test_a_failed_login_does_not_record_the_attempted_phone(self, app, db):
        """Otherwise the audit log becomes a list of guessed phone numbers."""
        client = app.test_client()
        client.post("/login", data={"identifier": "+201999999999", "password": "x"})
        entry = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "auth.login_failed")
        )
        assert "201999999999" not in str(entry.after_json)


class TestViewer:
    """S8.2 — the admin viewer, and the fact that it is read-only."""

    @staticmethod
    def _actions_shown(app, user, **query) -> list[str]:
        """The actions of the rendered entries, ignoring the filter dropdown.

        The dropdown lists every known action, so a naive "not in body" check
        would match an <option> and pass while the filter did nothing.
        """
        import re

        client = app.test_client()
        login(client, user)
        body = client.get("/admin/audit", query_string=query).get_data(as_text=True)
        return re.findall(r'<strong class="ltr">([a-z_.]+)</strong>', body)

    def test_it_renders_with_entries(self, app, world):
        assert "course.create" in self._actions_shown(app, world["admin"])

    def test_it_filters_by_action(self, app, world):
        shown = self._actions_shown(app, world["admin"], action="enrollment.create")
        assert set(shown) == {"enrollment.create"}

    def test_it_filters_by_actor(self, app, db, world):
        other = make_user(Role.ASSISTANT, phone="+201000009902", name="Mona")
        teaching_service.add_homework(other, world["course"], text="from Mona")

        shown = self._actions_shown(app, world["admin"], actor_id=other.id)
        assert shown == ["homework.create"]

    def test_it_filters_by_date_range(self, app, world):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        assert self._actions_shown(app, world["admin"], since=tomorrow) == []

    def test_it_shows_the_before_and_after(self, app, world):
        enrollment_service.set_payment_status(
            world["admin"], world["course"].enrollments[0], PaymentStatus.PAID
        )
        client = app.test_client()
        login(client, world["admin"])
        body = client.get(
            "/admin/audit", query_string={"action": "enrollment.payment_changed"}
        ).get_data(as_text=True)
        assert "unpaid" in body and "paid" in body

    def test_only_admin_can_read_it(self, app, world):
        for user in (world["teacher"], world["parent"], world["student"]):
            client = app.test_client()
            login(client, user)
            assert client.get("/admin/audit").status_code == 403

    def test_an_assistant_cannot_read_it(self, app, db, world):
        assistant = make_user(Role.ASSISTANT, phone="+201000009903")
        client = app.test_client()
        login(client, assistant)
        assert client.get("/admin/audit").status_code == 403

    def test_there_is_no_route_that_edits_or_deletes_an_audit_row(self, app):
        """An audit trail an admin can edit is not an audit trail."""
        writable = [
            rule.rule
            for rule in app.url_map.iter_rules()
            if "audit" in rule.rule.lower() and MUTATING_METHODS & rule.methods
        ]
        assert writable == []

    def test_the_service_exposes_no_way_to_change_history(self):
        forbidden = {"update", "delete", "purge", "edit", "remove"}
        exposed = {
            name
            for name in dir(audit_service)
            if not name.startswith("_") and any(word in name for word in forbidden)
        }
        assert exposed == set()
