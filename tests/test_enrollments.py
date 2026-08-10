"""Sprints S5.1–S5.4 — enrolment, booking status, payment status.

The portal is read-only about all of this: the guarantee is that the blueprint
contains no route that can change an enrolment (S5.4), not that a template
happens to omit the buttons.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.enums import BookingStatus, PaymentStatus, Role
from app.services import enrollments as enrollment_service
from tests.conftest import accept_current_terms, link_parent, login, make_course, make_user

SUNDAY = 6
WEDNESDAY = 2


@pytest.fixture
def world(app, db, seeded_terms, seeded_course_types, admin):
    teacher = make_user(Role.TEACHER, phone="+201011113001", name="Ahmed")
    accept_current_terms(teacher)
    student = make_user(Role.STUDENT, phone="+201055553001", name="Youssef")
    accept_current_terms(student)
    parent = make_user(Role.PARENT, phone="+201099993001", name="Adel")
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
        trial_enabled=True,
    )
    return {
        "admin": admin,
        "teacher": teacher,
        "student": student,
        "parent": parent,
        "course": course,
    }


class TestEnrolling:
    def test_it_links_the_student(self, app, world):
        enrollment_service.enroll(world["admin"], world["course"], world["student"])
        assert world["student"].id in {e.student_id for e in world["course"].enrollments}

    def test_it_starts_booked_and_unpaid(self, app, world):
        e = enrollment_service.enroll(world["admin"], world["course"], world["student"])
        assert e.booking_status is BookingStatus.BOOKED
        assert e.payment_status is PaymentStatus.UNPAID
        assert e.paid_at is None

    def test_the_amount_snapshots_the_course_price(self, app, world):
        """Raising the price later must not rewrite what this family owes."""
        e = enrollment_service.enroll(world["admin"], world["course"], world["student"])
        assert e.amount_due == Decimal("900.00")

        world["course"].price_egp = Decimal("1200")
        app.extensions["sqlalchemy"].session.commit()
        assert e.amount_due == Decimal("900.00")

    def test_double_enrolment_is_refused(self, app, world):
        enrollment_service.enroll(world["admin"], world["course"], world["student"])
        with pytest.raises(enrollment_service.EnrollmentError):
            enrollment_service.enroll(world["admin"], world["course"], world["student"])

    def test_only_students_can_be_enrolled(self, app, world):
        with pytest.raises(enrollment_service.EnrollmentError):
            enrollment_service.enroll(world["admin"], world["course"], world["teacher"])


class TestBookingStatus:
    @pytest.fixture
    def enrollment(self, app, world):
        return enrollment_service.enroll(
            world["admin"], world["course"], world["student"]
        )

    def test_it_can_be_changed(self, app, world, enrollment):
        enrollment_service.set_booking_status(
            world["admin"], enrollment, BookingStatus.NOT_BOOKED
        )
        assert enrollment.booking_status is BookingStatus.NOT_BOOKED

    def test_the_change_is_audited(self, app, db, world, enrollment):
        enrollment_service.set_booking_status(
            world["admin"], enrollment, BookingStatus.NOT_BOOKED
        )
        entry = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "enrollment.booking_changed")
        )
        assert entry is not None
        assert entry.before_json["booking_status"] == "booked"
        assert entry.after_json["booking_status"] == "not_booked"


class TestTrialGating:
    """Sprint S5.3 — `trial_enabled` on the course gates the trial status."""

    def test_trial_is_allowed_when_the_course_enables_it(self, app, world):
        e = enrollment_service.enroll(
            world["admin"],
            world["course"],
            world["student"],
            booking_status=BookingStatus.TRIAL,
        )
        assert e.booking_status is BookingStatus.TRIAL

    def test_trial_is_refused_when_the_course_does_not(self, app, db, world, admin):
        no_trial = make_course(
            admin,
            teacher=world["teacher"],
            name="No trials",
            course_type_code="sat_intermediate",
            slots=[{"weekday": 0, "start_time": "09:00"}],
            trial_enabled=False,
        )
        with pytest.raises(enrollment_service.EnrollmentError):
            enrollment_service.enroll(
                admin, no_trial, world["student"], booking_status=BookingStatus.TRIAL
            )

    def test_switching_to_trial_later_is_gated_too(self, app, db, world, admin):
        no_trial = make_course(
            admin,
            teacher=world["teacher"],
            name="No trials",
            course_type_code="sat_intermediate",
            slots=[{"weekday": 0, "start_time": "09:00"}],
            trial_enabled=False,
        )
        e = enrollment_service.enroll(admin, no_trial, world["student"])
        with pytest.raises(enrollment_service.EnrollmentError):
            enrollment_service.set_booking_status(admin, e, BookingStatus.TRIAL)

    def test_booking_is_staff_only_with_no_public_path(self, app, client):
        """Open question 8 is unanswered: trials are assistant-set only.

        Every route that can create or change a booking must live under the
        staff prefix, and none may be reachable anonymously. When question 8 is
        answered with "parents can request a trial", this test is the one that
        has to change — deliberately.
        """
        booking_routes = [
            rule.rule
            for rule in app.url_map.iter_rules()
            if ("trial" in rule.rule.lower() or "book" in rule.rule.lower())
        ]
        assert booking_routes, "expected at least the staff booking endpoint"
        assert all(r.startswith("/assistant/") for r in booking_routes), booking_routes

        for rule in booking_routes:
            probe = rule.replace("<int:enrollment_id>", "1")
            response = client.post(probe, follow_redirects=False)
            assert response.status_code == 302
            assert "/login" in response.headers["Location"]


class TestPaymentStatus:
    @pytest.fixture
    def enrollment(self, app, world):
        return enrollment_service.enroll(
            world["admin"], world["course"], world["student"]
        )

    def test_marking_paid_stamps_the_time(self, app, world, enrollment):
        enrollment_service.set_payment_status(
            world["admin"], enrollment, PaymentStatus.PAID
        )
        assert enrollment.payment_status is PaymentStatus.PAID
        assert enrollment.paid_at is not None
        assert enrollment.paid_at.tzinfo is not None  # aware UTC, per §2.3

    def test_reverting_to_unpaid_clears_the_time(self, app, world, enrollment):
        """Otherwise a later report shows an unpaid row carrying a payment date."""
        enrollment_service.set_payment_status(
            world["admin"], enrollment, PaymentStatus.PAID
        )
        enrollment_service.set_payment_status(
            world["admin"], enrollment, PaymentStatus.UNPAID
        )
        assert enrollment.payment_status is PaymentStatus.UNPAID
        assert enrollment.paid_at is None

    def test_every_change_is_audited_with_the_actor(self, app, db, world, enrollment):
        enrollment_service.set_payment_status(
            world["admin"], enrollment, PaymentStatus.PAID
        )
        entry = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "enrollment.payment_changed")
        )
        assert entry is not None
        assert entry.actor_id == world["admin"].id
        assert entry.after_json["payment_status"] == "paid"


class TestAmountDue:
    @pytest.fixture
    def enrollment(self, app, world):
        return enrollment_service.enroll(
            world["admin"], world["course"], world["student"]
        )

    def test_it_can_be_adjusted(self, app, world, enrollment):
        enrollment_service.set_amount_due(world["admin"], enrollment, "750.50")
        assert enrollment.amount_due == Decimal("750.50")

    @pytest.mark.parametrize("bad", ["abc", "1,000", "--5"])
    def test_a_non_number_is_refused(self, app, world, enrollment, bad):
        with pytest.raises(enrollment_service.EnrollmentError):
            enrollment_service.set_amount_due(world["admin"], enrollment, bad)

    def test_a_negative_amount_is_refused(self, app, world, enrollment):
        with pytest.raises(enrollment_service.EnrollmentError):
            enrollment_service.set_amount_due(world["admin"], enrollment, "-10")

    def test_blank_means_zero(self, app, world, enrollment):
        enrollment_service.set_amount_due(world["admin"], enrollment, "")
        assert enrollment.amount_due == Decimal("0")


class TestOutstandingReport:
    def test_it_lists_unpaid_enrolments(self, app, world):
        enrollment_service.enroll(world["admin"], world["course"], world["student"])
        outstanding = enrollment_service.unpaid_for(world["admin"])
        assert len(outstanding) == 1
        assert enrollment_service.outstanding_total(outstanding) == Decimal("900.00")

    def test_paid_enrolments_drop_out(self, app, world):
        e = enrollment_service.enroll(world["admin"], world["course"], world["student"])
        enrollment_service.set_payment_status(world["admin"], e, PaymentStatus.PAID)
        assert enrollment_service.unpaid_for(world["admin"]) == []

    def test_not_booked_enrolments_are_not_chased_for_money(self, app, world):
        e = enrollment_service.enroll(world["admin"], world["course"], world["student"])
        enrollment_service.set_booking_status(
            world["admin"], e, BookingStatus.NOT_BOOKED
        )
        assert enrollment_service.unpaid_for(world["admin"]) == []

    def test_a_teacher_sees_only_their_own_courses(self, app, db, world, admin):
        enrollment_service.enroll(admin, world["course"], world["student"])
        other_teacher = make_user(Role.TEACHER, phone="+201011113099")
        assert enrollment_service.unpaid_for(other_teacher) == []
        assert len(enrollment_service.unpaid_for(world["teacher"])) == 1


class TestPortalIsReadOnly:
    """Sprint S5.4 — the guarantee is structural, not cosmetic."""

    def test_the_portal_has_no_enrolment_mutation_route(self, app):
        mutating = [
            rule.rule
            for rule in app.url_map.iter_rules()
            if rule.rule.startswith("/portal")
            and {"POST", "PATCH", "PUT", "DELETE"} & rule.methods
        ]
        # Only the parent's child switcher, which writes to the session cookie.
        assert mutating == ["/portal/child/<int:student_id>/select"]

    def test_a_student_cannot_post_to_the_staff_endpoints(self, app, world):
        e = enrollment_service.enroll(
            world["admin"], world["course"], world["student"]
        )
        client = app.test_client()
        login(client, world["student"])
        for path in ("booking", "payment", "amount"):
            response = client.post(
                f"/assistant/enrollments/{e.id}/{path}", data={"status": "paid"}
            )
            assert response.status_code == 403
        assert e.payment_status is PaymentStatus.UNPAID

    def test_a_parent_cannot_mark_their_childs_fees_paid(self, app, world):
        e = enrollment_service.enroll(
            world["admin"], world["course"], world["student"]
        )
        client = app.test_client()
        login(client, world["parent"])
        response = client.post(
            f"/assistant/enrollments/{e.id}/payment", data={"status": "paid"}
        )
        assert response.status_code == 403
        assert e.payment_status is PaymentStatus.UNPAID

    def test_the_portal_shows_the_statuses_read_only(self, app, world):
        e = enrollment_service.enroll(
            world["admin"], world["course"], world["student"]
        )
        enrollment_service.set_payment_status(world["admin"], e, PaymentStatus.PAID)

        client = app.test_client()
        login(client, world["student"])
        body = client.get(
            f"/portal/courses/{world['course'].id}"
        ).get_data(as_text=True)
        assert "900" in body  # the amount due
        assert "/assistant/enrollments" not in body  # no staff controls leaked


class TestStaffRoutes:
    def test_the_controls_work_over_http(self, app, world):
        e = enrollment_service.enroll(
            world["admin"], world["course"], world["student"]
        )
        client = app.test_client()
        login(client, world["admin"])

        client.post(f"/assistant/enrollments/{e.id}/payment", data={"status": "paid"})
        assert e.payment_status is PaymentStatus.PAID

        client.post(f"/assistant/enrollments/{e.id}/booking", data={"status": "trial"})
        assert e.booking_status is BookingStatus.TRIAL

        client.post(f"/assistant/enrollments/{e.id}/amount", data={"amount_due": "450"})
        assert e.amount_due == Decimal("450")

    def test_a_teacher_cannot_reach_the_payments_page(self, app, world):
        client = app.test_client()
        login(client, world["teacher"])
        assert client.get("/assistant/payments").status_code == 403

    def test_the_payments_page_renders_with_data(self, app, world):
        enrollment_service.enroll(world["admin"], world["course"], world["student"])
        client = app.test_client()
        login(client, world["admin"])
        body = client.get("/assistant/payments").get_data(as_text=True)
        assert "Youssef" in body
        assert "900" in body

    def test_the_admin_overview_shows_what_is_outstanding(self, app, world):
        enrollment_service.enroll(world["admin"], world["course"], world["student"])
        client = app.test_client()
        login(client, world["admin"])
        body = client.get("/admin/").get_data(as_text=True)
        assert "900" in body
