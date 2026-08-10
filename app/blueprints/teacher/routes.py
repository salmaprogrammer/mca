"""Teacher area (sprints S2.6, S3.3, S3.5, plus the P1 role wall).

Everything resolves through `scoping`, which can only return this teacher's own
courses and sessions. There is no code path to another teacher's data — that is
the brief's core requirement.
"""

from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, send_file, url_for
from flask_babel import gettext as _
from flask_login import current_user

from app.blueprints.teacher import bp
from app.decorators import require_role
from app.labels import label_for
from app.models.course import WEEK_ORDER
from app.models.enums import AttendanceStatus, Role
from app.services import attendance as attendance_service
from app.services import dashboard as dashboard_service
from app.services import sessions as session_service
from app.services import storage
from app.services import teaching as teaching_service
from app.services.scoping import (
    feedback_for,
    get_course_or_404,
    get_material_or_404,
    get_session_or_404,
    homework_for,
    materials_for,
    sessions_for,
    students_for,
)


@bp.route("/")
@require_role(Role.TEACHER)
def home():
    return render_template(
        "teacher/home.html",
        dash=dashboard_service.teacher_dashboard(current_user),
    )


@bp.route("/courses/<int:course_id>")
@require_role(Role.TEACHER)
def course_detail(course_id: int):
    course = get_course_or_404(current_user, course_id)
    held, total = session_service.round_progress(course)
    return render_template(
        "teacher/course_detail.html",
        course=course,
        sessions=sessions_for(current_user, course_id=course.id),
        held=held,
        total=total,
        homework=homework_for(current_user, course_id=course.id, limit=10),
        feedback=feedback_for(current_user, course_id=course.id, limit=10),
        materials=materials_for(current_user, course_id=course.id),
    )


@bp.route("/sessions/<int:session_id>")
@require_role(Role.TEACHER)
def session_detail(session_id: int):
    session = get_session_or_404(current_user, session_id)
    return render_template(
        "teacher/session_detail.html",
        session=session,
        course=session.course,
        statuses=list(AttendanceStatus),
    )


@bp.route("/sessions/<int:session_id>/students/<int:student_id>/mark", methods=["POST"])
@require_role(Role.TEACHER)
def mark_student(session_id: int, student_id: int):
    """A teacher may mark only their own courses' sessions.

    `get_session_or_404` filters by the teacher's own courses first, so a
    guessed session ID belonging to someone else is a 404, not a 403.
    """
    session = get_session_or_404(current_user, session_id)
    student = next(
        (s for s in students_for(current_user) if s.id == student_id), None
    )
    if student is None:
        abort(404)

    try:
        status = AttendanceStatus(request.form.get("status"))
    except ValueError:
        flash(_("Choose a valid attendance status."), "error")
        return redirect(url_for("teacher.session_detail", session_id=session.id))

    try:
        attendance_service.mark_student(current_user, session, student, status)
        flash(
            _(
                "%(student)s marked %(status)s.",
                student=student.full_name,
                status=label_for(status),
            ),
            "success",
        )
    except attendance_service.AttendanceError as exc:
        flash(str(exc), "error")
    return redirect(url_for("teacher.session_detail", session_id=session.id))


@bp.route("/courses/<int:course_id>/cover")
@require_role(Role.TEACHER)
def course_cover(course_id: int):
    course = get_course_or_404(current_user, course_id)
    if not course.cover_image_path:
        abort(404)
    try:
        return send_file(storage.resolve(course.cover_image_path))
    except (ValueError, FileNotFoundError):
        abort(404)


def _week_grid(courses) -> list[tuple[int, list]]:
    """The teacher's week, Saturday first."""
    by_day: dict[int, list] = {day: [] for day in WEEK_ORDER}
    for course in courses:
        for slot in course.slots:
            by_day[slot.weekday].append((slot, course))
    for day in by_day:
        by_day[day].sort(key=lambda pair: pair[0].start_time)
    return [(day, by_day[day]) for day in WEEK_ORDER]


# ------------------------------------------- materials, homework, feedback


@bp.route("/courses/<int:course_id>/materials", methods=["GET", "POST"])
@require_role(Role.TEACHER)
def course_materials(course_id: int):
    """Teachers own their own course materials (sprint S4.3)."""
    course = get_course_or_404(current_user, course_id)

    if request.method == "POST":
        try:
            teaching_service.add_material(
                current_user,
                course,
                title=request.form.get("title"),
                url=request.form.get("url"),
            )
            flash(_("Material added."), "success")
            return redirect(url_for("teacher.course_materials", course_id=course.id))
        except teaching_service.TeachingError as exc:
            flash(str(exc), "error")

    return render_template(
        "teacher/course_materials.html",
        course=course,
        materials=materials_for(current_user, course_id=course.id),
    )


@bp.route("/materials/<int:material_id>/delete", methods=["POST"])
@require_role(Role.TEACHER)
def material_delete(material_id: int):
    material = get_material_or_404(current_user, material_id)
    course_id = material.course_id
    teaching_service.delete_material(current_user, material)
    flash(_("Material removed."), "success")
    return redirect(url_for("teacher.course_materials", course_id=course_id))
