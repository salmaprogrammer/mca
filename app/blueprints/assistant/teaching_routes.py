"""Homework, feedback and materials for staff (sprints S4.1–S4.3)."""

from __future__ import annotations

from datetime import date

from flask import flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from app.blueprints.assistant import bp
from app.decorators import require_staff
from app.services import teaching as teaching_service
from app.services.scoping import (
    feedback_for,
    get_course_or_404,
    get_feedback_or_404,
    get_homework_or_404,
    get_material_or_404,
    get_student_or_404,
    homework_for,
    materials_for,
)


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


@bp.route("/courses/<int:course_id>/homework", methods=["GET", "POST"])
@require_staff
def course_homework(course_id: int):
    course = get_course_or_404(current_user, course_id)

    if request.method == "POST":
        try:
            teaching_service.add_homework(
                current_user,
                course,
                text=request.form.get("text"),
                for_date=_parse_date(request.form.get("for_date")),
            )
            flash(_("Homework saved."), "success")
            return redirect(url_for("assistant.course_homework", course_id=course.id))
        except teaching_service.TeachingError as exc:
            flash(str(exc), "error")

    return render_template(
        "assistant/course_homework.html",
        course=course,
        homework=homework_for(current_user, course_id=course.id),
        today=date.today(),
    )


@bp.route("/homework/<int:homework_id>/delete", methods=["POST"])
@require_staff
def homework_delete(homework_id: int):
    homework = get_homework_or_404(current_user, homework_id)
    course_id = homework.course_id
    teaching_service.delete_homework(current_user, homework)
    flash(_("Homework removed."), "success")
    return redirect(url_for("assistant.course_homework", course_id=course_id))


@bp.route("/courses/<int:course_id>/feedback", methods=["GET", "POST"])
@require_staff
def course_feedback(course_id: int):
    course = get_course_or_404(current_user, course_id)

    if request.method == "POST":
        student_id = request.form.get("student_id", type=int)
        try:
            student = get_student_or_404(current_user, student_id) if student_id else None
            if student is None:
                raise teaching_service.TeachingError(_("Choose a student."))
            teaching_service.add_feedback(
                current_user,
                course,
                student,
                text=request.form.get("text"),
                for_date=_parse_date(request.form.get("for_date")),
            )
            flash(_("Feedback saved."), "success")
            return redirect(url_for("assistant.course_feedback", course_id=course.id))
        except teaching_service.TeachingError as exc:
            flash(str(exc), "error")

    return render_template(
        "assistant/course_feedback.html",
        course=course,
        feedback=feedback_for(current_user, course_id=course.id),
        today=date.today(),
    )


@bp.route("/feedback/<int:feedback_id>/delete", methods=["POST"])
@require_staff
def feedback_delete(feedback_id: int):
    feedback = get_feedback_or_404(current_user, feedback_id)
    course_id = feedback.course_id
    teaching_service.delete_feedback(current_user, feedback)
    flash(_("Feedback removed."), "success")
    return redirect(url_for("assistant.course_feedback", course_id=course_id))


@bp.route("/courses/<int:course_id>/materials", methods=["GET", "POST"])
@require_staff
def course_materials(course_id: int):
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
            return redirect(url_for("assistant.course_materials", course_id=course.id))
        except teaching_service.TeachingError as exc:
            flash(str(exc), "error")

    return render_template(
        "assistant/course_materials.html",
        course=course,
        materials=materials_for(current_user, course_id=course.id),
    )


@bp.route("/materials/<int:material_id>/delete", methods=["POST"])
@require_staff
def material_delete(material_id: int):
    material = get_material_or_404(current_user, material_id)
    course_id = material.course_id
    teaching_service.delete_material(current_user, material)
    flash(_("Material removed."), "success")
    return redirect(url_for("assistant.course_materials", course_id=course_id))
