"""Enrolment, booking and payment (sprints S5.1–S5.4).

Enrol/unenrol were built in P2 because a course nobody can join is invisible to
the portal and unusable by P3 attendance; they live here now, with the booking
and payment state that P5 adds.

Money rules worth stating once:

* `amount_due` is a **snapshot of the course price at enrolment**. Raising a
  course's price later must not silently change what an existing family was
  asked to pay.
* Marking paid stamps `paid_at`. The audit log records every change too, but a
  plain column is what an "who is overdue" report needs without walking the
  audit trail.
* Refunds and mid-round withdrawals are **not** modelled (open question 10).
  Marking an enrolment back to unpaid clears `paid_at` and is audited, which is
  the honest minimum until that question is answered.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask_babel import gettext as _
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.base import utcnow
from app.models.course import Course, Enrollment
from app.models.enums import BookingStatus, CourseStatus, PaymentStatus, Role
from app.models.user import User
from app.services import audit


class EnrollmentError(ValueError):
    """Something the operator needs explained."""


# ------------------------------------------------------------- enrolment


def enroll(
    actor: User,
    course: Course,
    student: User,
    *,
    booking_status: BookingStatus | None = None,
) -> Enrollment:
    if student.role is not Role.STUDENT:
        raise EnrollmentError(_("Only students can be enrolled in a course."))

    existing = db.session.scalar(
        select(Enrollment).where(
            Enrollment.course_id == course.id, Enrollment.student_id == student.id
        )
    )
    if existing:
        raise EnrollmentError(
            _("%(student)s is already enrolled in this course.", student=student.full_name)
        )

    status = booking_status or BookingStatus.BOOKED
    _assert_trial_allowed(course, status)

    enrollment = Enrollment(
        course_id=course.id,
        student_id=student.id,
        booking_status=status,
        payment_status=PaymentStatus.UNPAID,
        amount_due=course.price_egp or Decimal("0"),
        created_by_id=actor.id,
    )
    db.session.add(enrollment)
    db.session.flush()
    audit.record(
        "enrollment.create",
        "enrollment",
        entity_id=enrollment.id,
        actor=actor,
        after=audit.snapshot(enrollment),
    )
    db.session.commit()
    return enrollment


def unenroll(actor: User, enrollment: Enrollment) -> None:
    before = audit.snapshot(enrollment)
    db.session.delete(enrollment)
    audit.record(
        "enrollment.delete",
        "enrollment",
        entity_id=before.get("id"),
        actor=actor,
        before=before,
    )
    db.session.commit()


# ------------------------------------------------------ booking & payment


def _assert_trial_allowed(course: Course, status: BookingStatus) -> None:
    """Sprint S5.3: `trial_enabled` on the course gates the trial status.

    Open question 8 is still open — trials are assistant-set only, and there is
    no public booking page.
    """
    if status is BookingStatus.TRIAL and not course.trial_enabled:
        raise EnrollmentError(
            _("Trial bookings are not enabled for this course.")
        )


def set_booking_status(
    actor: User, enrollment: Enrollment, status: BookingStatus
) -> Enrollment:
    _assert_trial_allowed(enrollment.course, status)

    before = audit.snapshot(enrollment, ["booking_status"])
    enrollment.booking_status = status
    audit.record(
        "enrollment.booking_changed",
        "enrollment",
        entity_id=enrollment.id,
        actor=actor,
        before=before,
        after=audit.snapshot(enrollment, ["booking_status"]),
    )
    db.session.commit()
    return enrollment


def set_payment_status(
    actor: User, enrollment: Enrollment, status: PaymentStatus
) -> Enrollment:
    before = audit.snapshot(enrollment, ["payment_status", "paid_at"])

    enrollment.payment_status = status
    # Reverting to unpaid must clear the timestamp, or a later report would
    # show an unpaid enrolment carrying a payment date.
    enrollment.paid_at = utcnow() if status is PaymentStatus.PAID else None

    audit.record(
        "enrollment.payment_changed",
        "enrollment",
        entity_id=enrollment.id,
        actor=actor,
        before=before,
        after=audit.snapshot(enrollment, ["payment_status", "paid_at"]),
    )
    db.session.commit()
    return enrollment


def set_amount_due(actor: User, enrollment: Enrollment, raw_amount) -> Enrollment:
    try:
        amount = Decimal(str(raw_amount if raw_amount not in (None, "") else "0"))
    except (InvalidOperation, ValueError) as exc:
        raise EnrollmentError(_("Enter a valid amount.")) from exc
    if amount < 0:
        raise EnrollmentError(_("The amount cannot be negative."))

    before = audit.snapshot(enrollment, ["amount_due"])
    enrollment.amount_due = amount
    audit.record(
        "enrollment.amount_changed",
        "enrollment",
        entity_id=enrollment.id,
        actor=actor,
        before=before,
        after=audit.snapshot(enrollment, ["amount_due"]),
    )
    db.session.commit()
    return enrollment


# ---------------------------------------------------------------- reports


def unpaid_for(actor: User, *, course_ids: list[int] | None = None) -> list[Enrollment]:
    """Outstanding enrolments across every course this user may see."""
    from app.services.scoping import courses_for

    visible = [c.id for c in courses_for(actor)]
    if course_ids is not None:
        visible = [cid for cid in visible if cid in course_ids]
    if not visible:
        return []

    return list(
        db.session.scalars(
            select(Enrollment)
            .options(
                selectinload(Enrollment.course).selectinload(Course.course_type),
                selectinload(Enrollment.student),
            )
            .join(Course, Course.id == Enrollment.course_id)
            .where(
                Enrollment.course_id.in_(visible),
                Enrollment.payment_status == PaymentStatus.UNPAID,
                Enrollment.booking_status != BookingStatus.NOT_BOOKED,
                Course.status != CourseStatus.ARCHIVED,
            )
            .order_by(Course.name)
        )
    )


def outstanding_total(enrollments: list[Enrollment]) -> Decimal:
    return sum((e.amount_due or Decimal("0")) for e in enrollments) or Decimal("0")
