"""Translatable labels for enum values.

`_(status.value)` looks like it works and does not: gettext extraction is
static, so a runtime value produces no msgid, the string never reaches the
catalogue, and it renders in English forever. Nothing fails — which is why the
literals have to be written out here, once, where `pybabel extract` can see
them.

`tests/test_i18n_coverage.py` fails if a template goes back to translating a
`.value` directly.
"""

from __future__ import annotations

from flask_babel import lazy_gettext as _l

from app.models.enums import (
    AttendanceStatus,
    BookingStatus,
    CourseStatus,
    MessageStatus,
    PaymentStatus,
    Role,
    SessionStatus,
)

ATTENDANCE_LABELS = {
    AttendanceStatus.PRESENT: _l("present"),
    AttendanceStatus.ABSENT: _l("absent"),
    AttendanceStatus.LATE: _l("late"),
    AttendanceStatus.EXCUSED: _l("excused"),
}

SESSION_STATUS_LABELS = {
    SessionStatus.SCHEDULED: _l("scheduled"),
    SessionStatus.HELD: _l("held"),
    SessionStatus.CANCELLED_BY_TEACHER: _l("cancelled by teacher"),
    SessionStatus.CANCELLED_BY_CENTER: _l("cancelled by the centre"),
    SessionStatus.RESCHEDULED: _l("moved"),
}

BOOKING_LABELS = {
    BookingStatus.TRIAL: _l("trial"),
    BookingStatus.BOOKED: _l("booked"),
    BookingStatus.NOT_BOOKED: _l("not booked"),
}

PAYMENT_LABELS = {
    PaymentStatus.PAID: _l("paid"),
    PaymentStatus.UNPAID: _l("unpaid"),
}

COURSE_STATUS_LABELS = {
    CourseStatus.DRAFT: _l("draft"),
    CourseStatus.ACTIVE: _l("active"),
    CourseStatus.ARCHIVED: _l("archived"),
}

ROLE_LABELS = {
    Role.ADMIN: _l("Admin"),
    Role.ASSISTANT: _l("Assistant"),
    Role.TEACHER: _l("Teacher"),
    Role.STUDENT: _l("Student"),
    Role.PARENT: _l("Parent"),
}

MESSAGE_STATUS_LABELS = {
    MessageStatus.PREPARED: _l("prepared"),
    MessageStatus.SENT: _l("sent"),
}

_ALL = {}
for mapping in (
    ATTENDANCE_LABELS,
    SESSION_STATUS_LABELS,
    BOOKING_LABELS,
    PAYMENT_LABELS,
    COURSE_STATUS_LABELS,
    ROLE_LABELS,
    MESSAGE_STATUS_LABELS,
):
    _ALL.update(mapping)


def label_for(value) -> str:
    """Translated label for any enum member above; falls back to its value."""
    if value is None:
        return "—"
    return str(_ALL.get(value, getattr(value, "value", value)))
