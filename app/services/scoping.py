"""Ownership-scoped queries (sprints S1.7, S2.6).

Ground rule 1 (PLAN.md §7): route handlers never query models directly. They
call something here, which takes the acting user first and can only return rows
that user is permitted to see.

Ground rule 2: an out-of-scope *row* is a 404, never a 403 — a 403 confirms the
ID exists, which is exactly the enumeration the brief warned about. Role-level
denial of a whole area is a 403 (see app/decorators.py); that leaks nothing,
since those URLs are public knowledge.
"""

from __future__ import annotations

from datetime import date

from flask import abort
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.course import Course, Enrollment
from app.models.enums import CourseStatus, Role
from app.models.user import ParentLink, User

# --------------------------------------------------------------- students


def students_for(actor: User) -> list[User]:
    """Every student this user may see."""
    if actor.role in (Role.ADMIN, Role.ASSISTANT):
        return list(
            db.session.scalars(
                select(User).where(User.role == Role.STUDENT).order_by(User.full_name)
            )
        )
    if actor.role is Role.PARENT:
        return list(
            db.session.scalars(
                select(User)
                .join(ParentLink, ParentLink.student_id == User.id)
                .where(ParentLink.parent_id == actor.id)
                .order_by(User.full_name)
            )
        )
    if actor.role is Role.STUDENT:
        return [actor]
    if actor.role is Role.TEACHER:
        # Teachers resolve students through their own courses only. There is no
        # code path from a teacher to the global student list.
        return list(
            db.session.scalars(
                select(User)
                .join(Enrollment, Enrollment.student_id == User.id)
                .join(Course, Course.id == Enrollment.course_id)
                .where(
                    Course.teacher_id == actor.id,
                    Course.status != CourseStatus.ARCHIVED,
                )
                .distinct()
                .order_by(User.full_name)
            )
        )
    return []


def get_student_or_404(actor: User, student_id: int) -> User:
    for student in students_for(actor):
        if student.id == student_id:
            return student
    abort(404)


# ---------------------------------------------------------------- courses


def _course_query():
    return select(Course).options(
        selectinload(Course.slots),
        selectinload(Course.course_type),
        selectinload(Course.teacher),
        selectinload(Course.enrollments).selectinload(Enrollment.student),
    )


def courses_for(actor: User, *, include_archived: bool = False) -> list[Course]:
    """Every course this user may see.

    - staff: all of them
    - teacher: only those they teach
    - student: only those they are enrolled in
    - parent: only those their linked children are enrolled in
    """
    query = _course_query()
    if not include_archived:
        query = query.where(Course.status != CourseStatus.ARCHIVED)

    if actor.role in (Role.ADMIN, Role.ASSISTANT):
        pass
    elif actor.role is Role.TEACHER:
        query = query.where(Course.teacher_id == actor.id)
    elif actor.role is Role.STUDENT:
        query = query.join(Enrollment, Enrollment.course_id == Course.id).where(
            Enrollment.student_id == actor.id
        )
    elif actor.role is Role.PARENT:
        child_ids = [child.id for child in students_for(actor)]
        if not child_ids:
            return []
        query = (
            query.join(Enrollment, Enrollment.course_id == Course.id)
            .where(Enrollment.student_id.in_(child_ids))
            .distinct()
        )
    else:
        return []

    return list(db.session.scalars(query.order_by(Course.name)))


def get_course_or_404(actor: User, course_id: int, *, include_archived: bool = False) -> Course:
    for course in courses_for(actor, include_archived=include_archived):
        if course.id == course_id:
            return course
    abort(404)


def courses_for_student(actor: User, student: User) -> list[Course]:
    """Courses for one specific student, still bounded by what `actor` may see.

    Used by the parent portal's child switcher: a parent viewing child A must
    not see child B's courses, and neither may leak another family's.
    """
    if student.id not in {s.id for s in students_for(actor)}:
        abort(404)

    visible_ids = {c.id for c in courses_for(actor)}
    query = (
        _course_query()
        .join(Enrollment, Enrollment.course_id == Course.id)
        .where(
            Enrollment.student_id == student.id,
            Course.status != CourseStatus.ARCHIVED,
        )
        .order_by(Course.name)
    )
    return [c for c in db.session.scalars(query) if c.id in visible_ids]


def enrollment_for(course: Course, student: User) -> Enrollment | None:
    for enrollment in course.enrollments:
        if enrollment.student_id == student.id:
            return enrollment
    return None


# --------------------------------------------------------- account admin


def users_manageable_by(actor: User) -> list[User]:
    """Accounts whose credentials this user may reset or deactivate."""
    if actor.role is Role.ADMIN:
        return list(db.session.scalars(select(User).order_by(User.role, User.full_name)))
    if actor.role is Role.ASSISTANT:
        return list(
            db.session.scalars(
                select(User)
                .where(User.role.in_([Role.TEACHER, Role.STUDENT, Role.PARENT]))
                .order_by(User.role, User.full_name)
            )
        )
    return []


def get_manageable_user_or_404(actor: User, user_id: int) -> User:
    for user in users_manageable_by(actor):
        if user.id == user_id:
            return user
    abort(404)


# --------------------------------------------------------------- sessions


def sessions_for(
    actor: User,
    *,
    course_id: int | None = None,
    on: date | None = None,
) -> list:
    """Sessions this user may see, always bounded by the courses they may see.

    A teacher cannot reach another teacher's session because the course filter
    is applied first, not because the session query is filtered afterwards.
    """
    from app.models.session import CourseSession

    visible_course_ids = [c.id for c in courses_for(actor, include_archived=True)]
    if not visible_course_ids:
        return []
    if course_id is not None:
        if course_id not in visible_course_ids:
            abort(404)
        visible_course_ids = [course_id]

    query = (
        select(CourseSession)
        .options(
            selectinload(CourseSession.course).selectinload(Course.course_type),
            selectinload(CourseSession.attendance),
            selectinload(CourseSession.teacher),
        )
        .where(CourseSession.course_id.in_(visible_course_ids))
    )
    if on is not None:
        query = query.where(CourseSession.session_date == on)

    return list(
        db.session.scalars(
            query.order_by(CourseSession.session_date, CourseSession.start_time)
        )
    )


# ------------------------------------------- homework, feedback, materials


def homework_for(
    actor: User, *, course_id: int | None = None, limit: int | None = None
) -> list:
    """Homework the user may read — it is course-wide, so course scope suffices."""
    from app.models.teaching import Homework

    ids = [c.id for c in courses_for(actor)]
    if course_id is not None:
        if course_id not in ids:
            abort(404)
        ids = [course_id]
    if not ids:
        return []

    query = (
        select(Homework)
        .options(selectinload(Homework.course).selectinload(Course.course_type))
        .where(Homework.course_id.in_(ids))
        .order_by(Homework.for_date.desc(), Homework.id.desc())
    )
    if limit:
        query = query.limit(limit)
    return list(db.session.scalars(query))


def feedback_for(
    actor: User,
    *,
    student: User | None = None,
    course_id: int | None = None,
    limit: int | None = None,
) -> list:
    """The tightest scope in the app: feedback is private to one family.

    Course visibility is *not* enough here. A student can see a course they are
    enrolled in, but must never see a classmate's feedback — so a student
    filter is applied on top for every non-staff role, and a parent is narrowed
    to their own linked children.
    """
    from app.models.teaching import Feedback

    course_ids = [c.id for c in courses_for(actor)]
    if course_id is not None:
        if course_id not in course_ids:
            abort(404)
        course_ids = [course_id]
    if not course_ids:
        return []

    query = (
        select(Feedback)
        .options(selectinload(Feedback.course).selectinload(Course.course_type))
        .where(Feedback.course_id.in_(course_ids))
    )

    if actor.role is Role.STUDENT:
        query = query.where(Feedback.student_id == actor.id)
    elif actor.role is Role.PARENT:
        child_ids = [child.id for child in students_for(actor)]
        if not child_ids:
            return []
        query = query.where(Feedback.student_id.in_(child_ids))
    # Admin, assistant and the course's own teacher read the course's feedback;
    # `courses_for` has already limited a teacher to their own courses.

    if student is not None:
        # Asking about one student must still respect the narrowing above.
        if actor.role in (Role.STUDENT, Role.PARENT) and student.id not in {
            s.id for s in students_for(actor)
        }:
            abort(404)
        query = query.where(Feedback.student_id == student.id)

    query = query.order_by(Feedback.for_date.desc(), Feedback.id.desc())
    if limit:
        query = query.limit(limit)
    return list(db.session.scalars(query))


def materials_for(actor: User, *, course_id: int | None = None) -> list:
    from app.models.teaching import Material

    ids = [c.id for c in courses_for(actor)]
    if course_id is not None:
        if course_id not in ids:
            abort(404)
        ids = [course_id]
    if not ids:
        return []

    return list(
        db.session.scalars(
            select(Material)
            .where(Material.course_id.in_(ids))
            .order_by(Material.created_at.desc())
        )
    )


def get_homework_or_404(actor: User, homework_id: int):
    from app.models.teaching import Homework

    row = db.session.get(Homework, homework_id)
    if row is None or row.course_id not in {c.id for c in courses_for(actor)}:
        abort(404)
    return row


def get_feedback_or_404(actor: User, feedback_id: int):
    """Goes through `feedback_for`, so the per-student narrowing applies."""
    from app.models.teaching import Feedback

    row = db.session.get(Feedback, feedback_id)
    if row is None or row.id not in {f.id for f in feedback_for(actor)}:
        abort(404)
    return row


def get_material_or_404(actor: User, material_id: int):
    from app.models.teaching import Material

    row = db.session.get(Material, material_id)
    if row is None or row.course_id not in {c.id for c in courses_for(actor)}:
        abort(404)
    return row


def get_session_or_404(actor: User, session_id: int):
    """404 for a session outside this user's courses — the ID is the secret."""
    from app.models.session import CourseSession

    session = db.session.get(CourseSession, session_id)
    if session is None:
        abort(404)
    if session.course_id not in {c.id for c in courses_for(actor, include_archived=True)}:
        abort(404)
    return session
