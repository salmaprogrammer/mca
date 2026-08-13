"""Course catalogue routes for staff (sprints S2.2–S2.6)."""

from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user
from sqlalchemy import select

from app.blueprints.assistant import bp
from app.blueprints.assistant.course_forms import CourseForm, parse_slots, weekday_choices
from app.decorators import require_staff
from app.extensions import db
from app.models.course import Course, CourseType, Enrollment
from app.models.enums import BookingStatus, PaymentStatus, Role
from app.models.user import User
from app.services import courses as course_service
from app.services import enrollments as enrollment_service
from app.services.scheduling import ProposedSlot, ScheduleConflictError, find_conflicts
from app.services.scoping import courses_for, get_course_or_404, students_for

DEFAULT_DURATION = 90


def _teachers() -> list[User]:
    return list(
        db.session.scalars(
            select(User).where(User.role == Role.TEACHER).order_by(User.full_name)
        )
    )


def _slot_count_for(course_type_id: int | None) -> int:
    if not course_type_id:
        return 1
    course_type = db.session.get(CourseType, int(course_type_id))
    return course_type.sessions_per_week if course_type else 1


@bp.route("/courses")
@require_staff
def courses():
    return render_template(
        "assistant/courses.html",
        courses=courses_for(current_user, include_archived=True),
    )


@bp.route("/courses/new", methods=["GET", "POST"])
@require_staff
def course_new():
    course_types = course_service.all_course_types()
    if not course_types:
        # Happens on a fresh install where `flask seed-course-types` was
        # skipped. Say so rather than crashing on an empty choice list.
        flash(
            _("No course types are set up yet. Run “flask seed-course-types” first."),
            "error",
        )
        return redirect(url_for("assistant.courses"))

    form = CourseForm()
    form.load_choices(course_types, _teachers())

    slot_count = _slot_count_for(
        request.form.get("course_type_id") or course_types[0].id
    )
    submitted_slots = parse_slots(request.form, slot_count)

    if form.validate_on_submit():
        try:
            course = course_service.create_course(
                current_user,
                name=form.name.data,
                course_type_id=form.course_type_id.data,
                teacher_id=form.teacher_id.data,
                slots=submitted_slots,
                description=form.description.data,
                price_egp=form.price_egp.data or 0,
                trial_enabled=form.trial_enabled.data,
                start_date=form.start_date.data,
                cover_image=form.cover_image.data,
                duration_minutes=DEFAULT_DURATION,
            )
            flash(_("Course created."), "success")
            return redirect(url_for("assistant.course_detail", course_id=course.id))
        except ScheduleConflictError as exc:
            for conflict in exc.conflicts:
                flash(conflict.message, "error")
        except course_service.CourseError as exc:
            flash(str(exc), "error")

    return render_template(
        "assistant/course_form.html",
        form=form,
        course=None,
        slot_count=slot_count,
        submitted_slots=submitted_slots,
        weekdays=weekday_choices(),
        default_duration=DEFAULT_DURATION,
    )


@bp.route("/courses/<int:course_id>")
@require_staff
def course_detail(course_id: int):
    course = get_course_or_404(current_user, course_id, include_archived=True)
    enrolled_ids = {e.student_id for e in course.enrollments}
    return render_template(
        "assistant/course_detail.html",
        course=course,
        available_students=[
            s for s in students_for(current_user) if s.id not in enrolled_ids
        ],
        booking_statuses=list(BookingStatus),
        payment_statuses=list(PaymentStatus),
    )


@bp.route("/courses/<int:course_id>/edit", methods=["GET", "POST"])
@require_staff
def course_edit(course_id: int):
    course = get_course_or_404(current_user, course_id, include_archived=True)
    form = CourseForm(obj=course if request.method == "GET" else None)
    form.load_choices(course_service.all_course_types(), _teachers())

    if request.method == "GET":
        form.course_type_id.data = course.course_type_id
        form.teacher_id.data = course.teacher_id
        slot_count = course.course_type.sessions_per_week
        submitted_slots = [
            {"weekday": s.weekday, "start_time": f"{s.start_time:%H:%M}"}
            for s in course.slots_ordered_for_display()
        ]
    else:
        slot_count = _slot_count_for(request.form.get("course_type_id"))
        submitted_slots = parse_slots(request.form, slot_count)

    if form.validate_on_submit():
        try:
            course_service.update_course(
                current_user,
                course,
                name=form.name.data,
                course_type_id=form.course_type_id.data,
                teacher_id=form.teacher_id.data,
                slots=submitted_slots,
                description=form.description.data,
                price_egp=form.price_egp.data or 0,
                trial_enabled=form.trial_enabled.data,
                start_date=form.start_date.data,
                cover_image=form.cover_image.data,
                duration_minutes=DEFAULT_DURATION,
            )
            flash(_("Course updated."), "success")
            return redirect(url_for("assistant.course_detail", course_id=course.id))
        except ScheduleConflictError as exc:
            for conflict in exc.conflicts:
                flash(conflict.message, "error")
        except course_service.CourseError as exc:
            flash(str(exc), "error")

    return render_template(
        "assistant/course_form.html",
        form=form,
        course=course,
        slot_count=slot_count,
        submitted_slots=submitted_slots,
        weekdays=weekday_choices(),
        default_duration=DEFAULT_DURATION,
    )


@bp.route("/courses/<int:course_id>/archive", methods=["POST"])
@require_staff
def course_archive(course_id: int):
    course = get_course_or_404(current_user, course_id)
    course_service.archive_course(current_user, course)
    flash(_("Course archived."), "success")
    return redirect(url_for("assistant.courses"))
    @bp.route("/courses/<int:course_id>/delete", methods=["POST"])
@require_staff
def course_delete(course_id: int):
    course = get_course_or_404(current_user, course_id, include_archived=True)
    try:
        course_service.delete_course(current_user, course)
        flash(_("Course deleted."), "success")
        return redirect(url_for("assistant.courses"))
    except course_service.CourseError as exc:
        flash(str(exc), "error")
        return redirect(url_for("assistant.course_detail", course_id=course.id))


# ------------------------------------------------------------ HTMX bits


@bp.route("/courses/slot-rows", methods=["POST"])
@require_staff
def course_slot_rows():
    """Re-render the slot inputs when the course type changes."""
    slot_count = _slot_count_for(request.form.get("course_type_id"))
    return render_template(
        "partials/slot_rows.html",
        slot_count=slot_count,
        submitted_slots=parse_slots(request.form, slot_count),
        weekdays=weekday_choices(),
    )


@bp.route("/courses/check-conflicts", methods=["POST"])
@require_staff
def course_check_conflicts():
    """Live preview only. The server still re-validates on submit — this is a
    convenience, never the enforcement point (sprint S2.5)."""
    teacher_id = request.form.get("teacher_id", type=int)
    slot_count = _slot_count_for(request.form.get("course_type_id"))
    exclude = request.form.get("course_id", type=int)

    conflicts = []
    if teacher_id:
        proposed = [
            ProposedSlot(
                weekday=int(s["weekday"]),
                start_time=_time_or_none(s["start_time"]),
                duration_minutes=DEFAULT_DURATION,
            )
            for s in parse_slots(request.form, slot_count)
            if _time_or_none(s["start_time"])
        ]
        if proposed:
            conflicts = find_conflicts(teacher_id, proposed, exclude_course_id=exclude)

    return render_template("partials/conflict_warning.html", conflicts=conflicts)


def _time_or_none(value: str):
    from datetime import time

    try:
        hour, minute = value.split(":")[:2]
        return time(hour=int(hour), minute=int(minute))
    except (ValueError, AttributeError):
        return None


# ----------------------------------------------------------- enrolments


@bp.route("/courses/<int:course_id>/enroll", methods=["POST"])
@require_staff
def course_enroll(course_id: int):
    course = get_course_or_404(current_user, course_id)
    student_id = request.form.get("student_id", type=int)
    if not student_id:
        abort(400)

    # 404s for any student outside this user's scope.
    from app.services.scoping import get_student_or_404

    student = get_student_or_404(current_user, student_id)

    try:
        enrollment_service.enroll(current_user, course, student)
        flash(_("%(student)s enrolled.", student=student.full_name), "success")
    except enrollment_service.EnrollmentError as exc:
        flash(str(exc), "error")

    return redirect(url_for("assistant.course_detail", course_id=course.id))


@bp.route("/enrollments/<int:enrollment_id>/remove", methods=["POST"])
@require_staff
def enrollment_remove(enrollment_id: int):
    enrollment = db.session.get(Enrollment, enrollment_id)
    if enrollment is None:
        abort(404)
    # Prove the course is in scope before touching the enrolment.
    get_course_or_404(current_user, enrollment.course_id, include_archived=True)
    course_id = enrollment.course_id
    enrollment_service.unenroll(current_user, enrollment)
    flash(_("Student removed from the course."), "success")
    return redirect(url_for("assistant.course_detail", course_id=course_id))


@bp.route("/courses/<int:course_id>/cover")
@require_staff
def course_cover(course_id: int):
    """Cover images are served through an authenticated route, never a raw
    static path, so an unlisted URL cannot be shared out of the centre."""
    course = get_course_or_404(current_user, course_id, include_archived=True)
    return _send_cover(course)


def _send_cover(course: Course):
    from flask import send_file

    from app.services import storage

    if not course.cover_image_path:
        abort(404)
    try:
        return send_file(storage.resolve(course.cover_image_path))
    except (ValueError, FileNotFoundError):
        abort(404)
