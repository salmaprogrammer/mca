"""Every status column in PLAN.md §2.2 as a Python enum.

Stored as VARCHAR + CHECK (`native_enum=False`) so SQLite and Postgres agree
and the values stay readable in a raw table dump.
"""

try:
    from enum import StrEnum
except ImportError:  # Python < 3.11 (e.g. PythonAnywhere's default 3.10)
    from enum import Enum

    class StrEnum(str, Enum):
        """Minimal backport: members compare/serialise as their string value."""

        def __str__(self) -> str:
            return str(self.value)

import sqlalchemy as sa


class Role(StrEnum):
    ADMIN = "admin"
    ASSISTANT = "assistant"
    TEACHER = "teacher"
    STUDENT = "student"
    PARENT = "parent"

    @property
    def is_staff(self) -> bool:
        return self in (Role.ADMIN, Role.ASSISTANT)


class TermsAudience(StrEnum):
    TEACHER = "teacher"
    STUDENT_PARENT = "student_parent"

    @classmethod
    def for_role(cls, role: Role) -> "TermsAudience | None":
        """Admin and assistant never see a terms screen (brief §Onboarding)."""
        if role is Role.TEACHER:
            return cls.TEACHER
        if role in (Role.STUDENT, Role.PARENT):
            return cls.STUDENT_PARENT
        return None


# --- declared now, used from P2/P3 onward (sprint S0.2) ---


class CourseStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class SessionStatus(StrEnum):
    SCHEDULED = "scheduled"
    HELD = "held"
    CANCELLED_BY_TEACHER = "cancelled_by_teacher"
    CANCELLED_BY_CENTER = "cancelled_by_center"
    RESCHEDULED = "rescheduled"


class AttendanceStatus(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"
    EXCUSED = "excused"


class BookingStatus(StrEnum):
    TRIAL = "trial"
    BOOKED = "booked"
    NOT_BOOKED = "not_booked"


class PaymentStatus(StrEnum):
    PAID = "paid"
    UNPAID = "unpaid"


class MessageStatus(StrEnum):
    """Only what the centre can actually know.

    Updates are sent by hand from the assistant's own WhatsApp, so there is no
    API to report back: `delivered` and `read` would be guesses, and `failed`
    has nobody to report it. PREPARED means the text is composed and waiting;
    SENT means a named assistant opened it in WhatsApp at a recorded time.
    """

    PREPARED = "prepared"
    SENT = "sent"


def enum_column(enum_cls, length: int = 32) -> sa.Enum:
    """Portable enum column storing the *value*, not the member name."""
    return sa.Enum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


class CourseCycle(StrEnum):
    MONTH = "month"
    ROUND = "round"
