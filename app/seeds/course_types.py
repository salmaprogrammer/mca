"""The six fixed course types from the brief (sprint S2.1).

These are the centre's products. Nothing in the app can create, edit or delete
them — only which teacher, schedule, price and description attach to a course
*instance* of a type.

Per open question 3 the type label is what students see, so these strings are
user-facing copy. Type 1 is the exception: its label is a pricing package with
no subject in it, so `has_own_subject=False` makes those courses display their
own name instead.

Type 6's `sessions_per_week` is 2 (open question 2, answered 2026-08-07):
14 sessions over 7 weeks.
"""

from app.models.enums import CourseCycle

COURSE_TYPES = [
    {
        "id": 1,
        "code": "monthly_4",
        "label_en": "Monthly — 4 sessions",
        "label_ar": "اشتراك شهري — ٤ حصص",
        "sessions_per_week": 1,
        "cycle": CourseCycle.MONTH,
        "total_sessions": 4,
        "has_own_subject": False,
    },
    {
        "id": 2,
        "code": "sat_intermediate",
        "label_en": "SAT Intermediate",
        "label_ar": "SAT متوسط",
        "sessions_per_week": 1,
        "cycle": CourseCycle.MONTH,
        "total_sessions": 5,
        "has_own_subject": True,
    },
    {
        "id": 3,
        "code": "gpa_course",
        "label_en": "GPA Course",
        "label_ar": "كورس الـ GPA",
        "sessions_per_week": 2,
        "cycle": CourseCycle.MONTH,
        "total_sessions": 8,
        "has_own_subject": True,
    },
    {
        "id": 4,
        "code": "sat_basics",
        "label_en": "SAT Basics",
        "label_ar": "أساسيات SAT",
        "sessions_per_week": 2,
        "cycle": CourseCycle.ROUND,
        "total_sessions": 10,
        "has_own_subject": True,
    },
    {
        "id": 5,
        "code": "est_basics",
        "label_en": "EST Basics",
        "label_ar": "أساسيات EST",
        "sessions_per_week": 2,
        "cycle": CourseCycle.ROUND,
        "total_sessions": 10,
        "has_own_subject": True,
    },
    {
        "id": 6,
        "code": "advanced",
        "label_en": "Advanced Course",
        "label_ar": "الكورس المتقدم",
        "sessions_per_week": 2,
        "cycle": CourseCycle.ROUND,
        "total_sessions": 14,
        "has_own_subject": True,
    },
]
