"""WhatsApp message centre for staff (sprints S6.3, S6.5).

Sending is manual. `whatsapp_open` records the hand-off and then redirects the
browser to a wa.me link, which is why `form-action` in the CSP lists that host
— browsers check redirect targets against it, and `'self'` alone would break
the button with nothing in the logs to explain why.
"""

from __future__ import annotations

from datetime import date

from flask import flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from app.blueprints.assistant import bp
from app.decorators import require_staff
from app.services import whatsapp as whatsapp_service
from app.services.scoping import get_student_or_404, students_for


def _parse_date(raw: str | None, fallback: date) -> date:
    if not raw:
        return fallback
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return fallback


@bp.route("/whatsapp")
@require_staff
def whatsapp_centre():
    """Per-student preview of exactly what will be sent, plus the log."""
    on = _parse_date(request.args.get("date"), date.today())

    previews = []
    for student in students_for(current_user):
        recipient = whatsapp_service.recipient_for(student)
        previews.append(
            {
                "student": student,
                "recipient": recipient,
                "body": whatsapp_service.build_daily_summary(
                    student,
                    on,
                    locale=(recipient.locale if recipient else None),
                ),
                "already": whatsapp_service.daily_message_for(student, on),
            }
        )

    return render_template(
        "assistant/whatsapp.html",
        on=on,
        previews=previews,
        waiting=whatsapp_service.waiting_to_be_sent(current_user),
        messages=whatsapp_service.messages_for(current_user, limit=50),
    )


@bp.route("/whatsapp/open/<int:student_id>", methods=["POST"])
@require_staff
def whatsapp_open(student_id: int):
    """Record the hand-off, then open WhatsApp with the text already typed.

    The redirect leaves the app, so a success flash here would never be seen —
    the page that proves it worked is WhatsApp itself, with the message in the
    box. Errors still redirect back, because those the assistant must see.
    """
    student = get_student_or_404(current_user, student_id)
    on = _parse_date(request.form.get("date"), date.today())
    force = request.form.get("force") == "1"

    try:
        message = whatsapp_service.hand_off(current_user, student, on=on, force=force)
    except whatsapp_service.WhatsAppError as exc:
        flash(str(exc), "error")
        return redirect(url_for("assistant.whatsapp_centre", date=on.isoformat()))

    return redirect(whatsapp_service.chat_link(message))


@bp.route("/whatsapp/prepare-all", methods=["POST"])
@require_staff
def whatsapp_prepare_all():
    """Compose today's update for everyone so they can be sent one by one.

    Deliberately does not send: there is no way to send in bulk from a personal
    WhatsApp account, and a button claiming otherwise would be a lie.
    """
    on = _parse_date(request.form.get("date"), date.today())
    counters = whatsapp_service.prepare_daily_batch(current_user, on)
    flash(
        _(
            "%(prepared)d update(s) prepared, %(skipped)d skipped. "
            "Each still needs sending from WhatsApp.",
            **counters,
        ),
        "info",
    )
    return redirect(url_for("assistant.whatsapp_centre", date=on.isoformat()))
