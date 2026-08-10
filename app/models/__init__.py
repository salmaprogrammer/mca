"""Model package — importing this registers every table with the metadata."""

from app.models.audit import AuditLog
from app.models.course import (
    WEEK_ORDER,
    WEEKDAY_LABELS,
    Course,
    CourseSlot,
    CourseType,
    Enrollment,
    weekday_label,
)
from app.models.enums import (
    AttendanceStatus,
    BookingStatus,
    CourseCycle,
    CourseStatus,
    MessageStatus,
    PaymentStatus,
    Role,
    SessionStatus,
    TermsAudience,
)
from app.models.messaging import WhatsAppMessage
from app.models.session import AttendanceRecord, CourseSession
from app.models.teaching import Feedback, Homework, Material
from app.models.terms import TermsAcceptance, TermsVersion
from app.models.user import ParentLink, StudentProfile, TeacherProfile, User

__all__ = [
    "WEEKDAY_LABELS",
    "WEEK_ORDER",
    "AttendanceRecord",
    "AttendanceStatus",
    "AuditLog",
    "BookingStatus",
    "Course",
    "CourseCycle",
    "CourseSession",
    "CourseSlot",
    "CourseStatus",
    "CourseType",
    "Enrollment",
    "Feedback",
    "Homework",
    "Material",
    "MessageStatus",
    "ParentLink",
    "PaymentStatus",
    "Role",
    "SessionStatus",
    "StudentProfile",
    "TeacherProfile",
    "TermsAcceptance",
    "TermsAudience",
    "TermsVersion",
    "User",
    "WhatsAppMessage",
    "weekday_label",
]
