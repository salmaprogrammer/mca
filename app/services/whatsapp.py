"""Daily WhatsApp updates (sprints S6.1, S6.3, S6.4, S6.6).

**Sent by hand.** The app composes each update and builds a click-to-chat link;
an assistant opens it in their own WhatsApp and presses send. There is no Meta
Cloud API, no access token, no webhook and no nightly job — the trade is that
nobody waits on business verification, and the cost is that delivery is a fact
about a person, not something the app can observe.

Open question 7, built as recommended: **one combined message per student per
day**, not one per course. A parent with a child in two courses gets one
update, not two.
"""

from __future__ import annotations

import urllib.parse
from datetime import date

from flask import current_app
from flask_babel import force_locale
from flask_babel import gettext as _
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.labels import label_for
from app.models.base import utcnow
from app.models.course import Course, Enrollment
from app.models.enums import CourseStatus, MessageStatus, Role
from app.models.messaging import WhatsAppMessage
from app.models.session import AttendanceRecord, CourseSession
from app.models.teaching import Feedback, Homework
from app.models.user import User
from app.services import audit


class WhatsAppError(ValueError):
    """Something the operator needs explained."""


# ------------------------------------------------------------- composition


def _courses_for_student(student: User) -> list[Course]:
    return list(
        db.session.scalars(
            select(Course)
            .options(selectinload(Course.course_type))
            .join(Enrollment, Enrollment.course_id == Course.id)
            .where(
                Enrollment.student_id == student.id,
                Course.status != CourseStatus.ARCHIVED,
            )
        )
    )


def build_daily_summary(student: User, on: date | None = None, locale: str | None = None) -> str:
    """Assemble one student's update for one day, in the reader's language.

    Deliberately says "no session today" rather than staying silent: a parent
    who gets a message with nothing in it learns nothing, and a message that
    omits a section looks like the section is broken.
    """
    on = on or date.today()
    locale = locale or "ar"
    courses = _courses_for_student(student)
    course_ids = [c.id for c in courses]

    with force_locale(locale):
        if not course_ids:
            return _(
                "Daily update for %(name)s — %(date)s\nNot enrolled in any course.",
                name=student.full_name,
                date=on.isoformat(),
            )

        attendance = list(
            db.session.scalars(
                select(AttendanceRecord)
                .join(CourseSession, CourseSession.id == AttendanceRecord.session_id)
                .options(
                    selectinload(AttendanceRecord.session).selectinload(CourseSession.course)
                )
                .where(
                    AttendanceRecord.student_id == student.id,
                    CourseSession.course_id.in_(course_ids),
                    CourseSession.session_date == on,
                )
            )
        )
        homework = list(
            db.session.scalars(
                select(Homework).where(
                    Homework.course_id.in_(course_ids), Homework.for_date == on
                )
            )
        )
        feedback = list(
            db.session.scalars(
                select(Feedback).where(
                    Feedback.course_id.in_(course_ids),
                    Feedback.student_id == student.id,
                    Feedback.for_date == on,
                )
            )
        )

        lines = [
            _(
                "Daily update for %(name)s — %(date)s",
                name=student.full_name,
                date=on.isoformat(),
            ),
            "",
        ]

        lines.append(_("Attendance:"))
        if attendance:
            for record in attendance:
                course_name = record.session.course.display_title(locale)
                if record.checked_in_at:
                    lines.append(
                        _(
                            "• %(course)s — %(status)s at %(time)s",
                            course=course_name,
                            status=label_for(record.status),
                            time=_local_time(record.checked_in_at),
                        )
                    )
                else:
                    lines.append(
                        _(
                            "• %(course)s — %(status)s",
                            course=course_name,
                            status=label_for(record.status),
                        )
                    )
        else:
            lines.append(_("• No session recorded today."))

        lines.append("")
        lines.append(_("Homework:"))
        if homework:
            lines.extend(f"• {item.text}" for item in homework)
        else:
            lines.append(_("• None assigned today."))

        lines.append("")
        lines.append(_("Feedback:"))
        if feedback:
            lines.extend(f"• {item.text}" for item in feedback)
        else:
            lines.append(_("• None logged today."))

        return "\n".join(lines)


def _local_time(value) -> str:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(current_app.config["TIMEZONE"])
    return f"{value.astimezone(tz):%H:%M}"


# ----------------------------------------------------------------- sending


def recipient_for(student: User) -> User | None:
    """Who the update goes to: a linked parent, else the student themselves."""
    for parent in student.parents:
        if parent.phone:
            return parent
    return student if student.phone else None


def daily_message_for(student: User, on: date) -> WhatsAppMessage | None:
    """The row claiming this student's daily slot, prepared or sent."""
    return db.session.scalar(
        select(WhatsAppMessage).where(
            WhatsAppMessage.student_id == student.id,
            WhatsAppMessage.batch_date == on,
        )
    )


def chat_link(message: WhatsAppMessage) -> str:
    """The click-to-chat URL that opens WhatsApp with the text already typed.

    wa.me wants bare digits — a leading `+` or a space and the link silently
    opens a chat with nobody.
    """
    base = current_app.config["WHATSAPP_LINK_BASE"].rstrip("/")
    number = "".join(ch for ch in message.to_phone if ch.isdigit())
    return f"{base}/{number}?text={urllib.parse.quote(message.body)}"


def prepare_daily_update(
    actor: User | None,
    student: User,
    *,
    on: date | None = None,
    force: bool = False,
) -> WhatsAppMessage:
    """Compose one student's update and record it as waiting to be sent.

    Nothing leaves the building here. The row exists so the text an assistant
    sent is the text the log shows, rather than something recomposed later
    from data that has since changed.
    """
    on = on or date.today()

    if student.role is not Role.STUDENT:
        raise WhatsAppError(_("Daily updates are about students."))

    existing = daily_message_for(student, on)
    if existing and not force:
        raise WhatsAppError(
            _("%(name)s already has today's update.", name=student.full_name)
        )

    recipient = recipient_for(student)
    if recipient is None or not recipient.phone:
        raise WhatsAppError(
            _("%(name)s has no contact number on file.", name=student.full_name)
        )

    locale = recipient.locale or current_app.config["BABEL_DEFAULT_LOCALE"]

    message = WhatsAppMessage(
        student_id=student.id,
        recipient_id=recipient.id,
        to_phone=recipient.phone,
        body=build_daily_summary(student, on, locale=locale),
        locale=locale,
        # A forced re-send is an extra message, not a second "daily update",
        # so it carries no batch_date and the unique constraint ignores it.
        batch_date=None if (existing and force) else on,
        created_by_id=actor.id if actor else None,
        status=MessageStatus.PREPARED,
    )
    db.session.add(message)
    db.session.flush()

    audit.record(
        "whatsapp.prepare",
        "whatsapp_message",
        entity_id=message.id,
        actor=actor,
        after={"student_id": student.id, "batch_date": str(on)},
    )
    db.session.commit()
    return message


def mark_sent(actor: User, message: WhatsAppMessage) -> WhatsAppMessage:
    """Record that a named person took this message to WhatsApp.

    Called when the assistant clicks the button, *before* the browser follows
    the link — so the log shows an attempt even if they close WhatsApp without
    pressing send. That is the honest direction to be wrong in: a message shown
    as sent that was not gets questioned, one shown as never touched does not.
    """
    if message.status is MessageStatus.SENT:
        return message

    message.status = MessageStatus.SENT
    message.sent_by_id = actor.id
    message.sent_at = utcnow()

    audit.record(
        "whatsapp.send",
        "whatsapp_message",
        entity_id=message.id,
        actor=actor,
        after={"student_id": message.student_id, "status": message.status.value},
    )
    db.session.commit()
    return message


def hand_off(
    actor: User, student: User, *, on: date | None = None, force: bool = False
) -> WhatsAppMessage:
    """What the "send on WhatsApp" button does: compose if needed, then mark.

    Re-using an already-prepared row matters — otherwise the assistant would
    send the text they read on screen while the log recorded a freshly composed
    one, and the two would differ the moment anybody edited today's homework.
    """
    on = on or date.today()
    existing = daily_message_for(student, on)

    if existing is None or force:
        message = prepare_daily_update(actor, student, on=on, force=force)
    elif existing.status is MessageStatus.SENT:
        raise WhatsAppError(
            _("%(name)s already has today's update.", name=student.full_name)
        )
    else:
        message = existing

    return mark_sent(actor, message)


def prepare_daily_batch(actor: User | None, on: date | None = None) -> dict[str, int]:
    """Compose today's update for every active student, ready to send.

    Never forces, so running it twice prepares once. Nothing is sent: each
    message still needs an assistant to open it in WhatsApp.
    """
    on = on or date.today()
    counters = {"prepared": 0, "skipped": 0}

    students = db.session.scalars(
        select(User).where(User.role == Role.STUDENT, User.is_active.is_(True))
    )
    for student in students:
        try:
            prepare_daily_update(actor, student, on=on)
        except WhatsAppError:
            counters["skipped"] += 1
            continue
        counters["prepared"] += 1
    return counters


# ----------------------------------------------------------------- reading


def messages_for(
    actor: User, *, student: User | None = None, limit: int | None = None
) -> list[WhatsAppMessage]:
    """Message history, scoped like everything else.

    A parent sees messages sent to them about their own children, and nothing
    else — the same family boundary feedback uses. They also see only what was
    actually sent: a prepared-but-unsent message appearing in a parent's
    history would tell them they received something nobody has sent yet.
    """
    from app.services.scoping import students_for

    query = select(WhatsAppMessage).options(
        selectinload(WhatsAppMessage.student),
        selectinload(WhatsAppMessage.recipient),
    )

    if actor.role in (Role.ADMIN, Role.ASSISTANT):
        pass
    elif actor.role is Role.PARENT:
        child_ids = [child.id for child in students_for(actor)]
        if not child_ids:
            return []
        query = query.where(
            WhatsAppMessage.student_id.in_(child_ids),
            WhatsAppMessage.recipient_id == actor.id,
            WhatsAppMessage.status == MessageStatus.SENT,
        )
    elif actor.role is Role.STUDENT:
        query = query.where(
            WhatsAppMessage.student_id == actor.id,
            WhatsAppMessage.status == MessageStatus.SENT,
        )
    else:
        # Teachers have no business reading the centre's messages to families.
        return []

    if student is not None:
        if actor.role in (Role.PARENT, Role.STUDENT) and student.id not in {
            s.id for s in students_for(actor)
        }:
            from flask import abort

            abort(404)
        query = query.where(WhatsAppMessage.student_id == student.id)

    query = query.order_by(WhatsAppMessage.created_at.desc())
    if limit:
        query = query.limit(limit)
    return list(db.session.scalars(query))


def waiting_to_be_sent(actor: User, limit: int = 20) -> list[WhatsAppMessage]:
    """Surfaced on the staff dashboard.

    This replaces the old "delivery failed" panel. With no API there is no
    failure to report, but there is a new way to let a family down: composing
    an update and never opening it in WhatsApp. That is what this shows.
    """
    if actor.role not in (Role.ADMIN, Role.ASSISTANT):
        return []
    return list(
        db.session.scalars(
            select(WhatsAppMessage)
            .options(selectinload(WhatsAppMessage.student))
            .where(WhatsAppMessage.status == MessageStatus.PREPARED)
            .order_by(WhatsAppMessage.created_at.desc())
            .limit(limit)
        )
    )
