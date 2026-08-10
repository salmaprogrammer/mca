"""Student and parent portal — read-only by design.

This blueprint deliberately contains no route that mutates a course, enrolment,
attendance record or payment status. That absence is the guarantee (sprint
S5.4). The child switcher only changes what the parent is looking at.
"""

from __future__ import annotations

from flask import abort, redirect, render_template, send_file, session, url_for
from flask_login import current_user

from app.blueprints.portal import bp
from app.decorators import require_role
from app.models.enums import Role
from app.services import attendance as attendance_service
from app.services import dashboard as dashboard_service
from app.services import storage
from app.services import whatsapp as whatsapp_service
from app.services.scoping import (
    courses_for_student,
    enrollment_for,
    feedback_for,
    get_course_or_404,
    get_student_or_404,
    homework_for,
    materials_for,
    students_for,
)


def _selected_student():
    """Which student this portal view is about."""
    if current_user.role is Role.STUDENT:
        return current_user

    children = students_for(current_user)
    if not children:
        return None

    chosen_id = session.get("portal_child_id")
    if chosen_id:
        for child in children:
            if child.id == chosen_id:
                return child
    return children[0]


@bp.route("/")
@require_role(Role.STUDENT, Role.PARENT)
def home():
    student = _selected_student()
    return render_template(
        "portal/home.html",
        dash=dashboard_service.portal_dashboard(current_user, student),
        student=student,
        enrollment_for=enrollment_for,
        children=students_for(current_user) if current_user.role is Role.PARENT else [],
    )


@bp.route("/messages")
@require_role(Role.STUDENT, Role.PARENT)
def messages():
    """What the centre has sent about this student (sprint S6.6)."""
    student = _selected_student()
    if student is None:
        return redirect(url_for("portal.home"))
    return render_template(
        "portal/messages.html",
        student=student,
        messages=whatsapp_service.messages_for(current_user, student=student),
    )


@bp.route("/attendance")
@require_role(Role.STUDENT, Role.PARENT)
def attendance():
    """Full attendance history for the selected student (sprint S3.5)."""
    student = _selected_student()
    if student is None:
        return redirect(url_for("portal.home"))

    courses = courses_for_student(current_user, student)
    records = attendance_service.history_for_student(
        student, course_ids=[c.id for c in courses]
    )
    return render_template(
        "portal/attendance.html",
        student=student,
        attendance=records,
        summary=attendance_service.summarise(records),
    )


@bp.route("/child/<int:student_id>/select", methods=["POST"])
@require_role(Role.PARENT)
def select_child(student_id: int):
    # 404s for any student not linked to this parent.
    student = get_student_or_404(current_user, student_id)
    session["portal_child_id"] = student.id
    return redirect(url_for("portal.home"))


@bp.route("/courses/<int:course_id>")
@require_role(Role.STUDENT, Role.PARENT)
def course_detail(course_id: int):
    course = get_course_or_404(current_user, course_id)
    student = _selected_student()
    return render_template(
        "portal/course_detail.html",
        course=course,
        student=student,
        enrollment=enrollment_for(course, student) if student else None,
        attendance=(
            attendance_service.history_for_student(student, course_ids=[course.id])
            if student
            else []
        ),
        homework=homework_for(current_user, course_id=course.id),
        feedback=(
            feedback_for(current_user, student=student, course_id=course.id)
            if student
            else []
        ),
        materials=materials_for(current_user, course_id=course.id),
    )


@bp.route("/courses/<int:course_id>/cover")
@require_role(Role.STUDENT, Role.PARENT)
def course_cover(course_id: int):
    course = get_course_or_404(current_user, course_id)
    if not course.cover_image_path:
        abort(404)
    try:
        return send_file(storage.resolve(course.cover_image_path))
    except (ValueError, FileNotFoundError):
        abort(404)


@bp.route("/students/<int:student_id>")
@require_role(Role.STUDENT, Role.PARENT)
def student_detail(student_id: int):
    """Proves the scoping wall in tests, and doubles as a direct child link."""
    student = get_student_or_404(current_user, student_id)
    return render_template(
        "portal/home.html",
        dash=dashboard_service.portal_dashboard(current_user, student),
        student=student,
        enrollment_for=enrollment_for,
        children=[],
    )
