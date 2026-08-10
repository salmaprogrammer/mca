"""Booking, payment and the outstanding-payments overview (sprints S5.2–S5.4)."""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from app.blueprints.assistant import bp
from app.decorators import require_staff
from app.extensions import db
from app.models.course import Enrollment
from app.models.enums import BookingStatus, PaymentStatus
from app.services import enrollments as enrollment_service
from app.services.scoping import courses_for, get_course_or_404


def _get_enrollment_or_404(enrollment_id: int) -> Enrollment:
    """Reached only through a course this user may see."""
    from flask import abort

    enrollment = db.session.get(Enrollment, enrollment_id)
    if enrollment is None:
        abort(404)
    get_course_or_404(current_user, enrollment.course_id, include_archived=True)
    return enrollment


@bp.route("/enrollments/<int:enrollment_id>/booking", methods=["POST"])
@require_staff
def enrollment_booking(enrollment_id: int):
    enrollment = _get_enrollment_or_404(enrollment_id)
    try:
        status = BookingStatus(request.form.get("status"))
    except ValueError:
        flash(_("Choose a valid booking status."), "error")
        return _back(enrollment)

    try:
        enrollment_service.set_booking_status(current_user, enrollment, status)
        flash(_("Booking status updated."), "success")
    except enrollment_service.EnrollmentError as exc:
        flash(str(exc), "error")
    return _back(enrollment)


@bp.route("/enrollments/<int:enrollment_id>/payment", methods=["POST"])
@require_staff
def enrollment_payment(enrollment_id: int):
    enrollment = _get_enrollment_or_404(enrollment_id)
    try:
        status = PaymentStatus(request.form.get("status"))
    except ValueError:
        flash(_("Choose a valid payment status."), "error")
        return _back(enrollment)

    enrollment_service.set_payment_status(current_user, enrollment, status)
    flash(_("Payment status updated."), "success")
    return _back(enrollment)


@bp.route("/enrollments/<int:enrollment_id>/amount", methods=["POST"])
@require_staff
def enrollment_amount(enrollment_id: int):
    enrollment = _get_enrollment_or_404(enrollment_id)
    try:
        enrollment_service.set_amount_due(
            current_user, enrollment, request.form.get("amount_due")
        )
        flash(_("Amount updated."), "success")
    except enrollment_service.EnrollmentError as exc:
        flash(str(exc), "error")
    return _back(enrollment)


@bp.route("/payments")
@require_staff
def payments():
    """Everything still owed, across every course this user may see."""
    course_id = request.args.get("course_id", type=int)
    visible = courses_for(current_user)
    outstanding = enrollment_service.unpaid_for(
        current_user, course_ids=[course_id] if course_id else None
    )
    return render_template(
        "assistant/payments.html",
        courses=visible,
        selected_course_id=course_id,
        outstanding=outstanding,
        total=enrollment_service.outstanding_total(outstanding),
        booking_statuses=list(BookingStatus),
        payment_statuses=list(PaymentStatus),
    )


def _back(enrollment: Enrollment):
    target = request.form.get("next")
    if target == "payments":
        return redirect(url_for("assistant.payments"))
    return redirect(url_for("assistant.course_detail", course_id=enrollment.course_id))
