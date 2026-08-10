"""Session and attendance routes for staff (sprints S3.1–S3.5)."""

from __future__ import annotations

from datetime import date, datetime, time

from flask import flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from app.blueprints.assistant import bp
from app.decorators import require_staff
from app.labels import label_for
from app.models.enums import AttendanceStatus
from app.services import attendance as attendance_service
from app.services import sessions as session_service
from app.services.scoping import (
    courses_for,
    get_course_or_404,
    get_session_or_404,
    get_student_or_404,
    sessions_for,
)


def _parse_date(raw: str | None, fallback: date | None = None) -> date | None:
    if not raw:
        return fallback
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return fallback


def _parse_status(raw: str | None) -> AttendanceStatus | None:
    try:
        return AttendanceStatus(raw)
    except ValueError:
        return None


@bp.route("/courses/<int:course_id>/generate-sessions", methods=["POST"])
@require_staff
def generate_sessions(course_id: int):
    course = get_course_or_404(current_user, course_id)
    try:
        created = session_service.generate_sessions(current_user, course)
    except session_service.SessionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("assistant.course_detail", course_id=course.id))

    if created:
        flash(_("%(count)d session(s) created.", count=len(created)), "success")
    else:
        flash(_("All sessions for this course already exist."), "info")
    return redirect(url_for("assistant.course_sessions", course_id=course.id))


@bp.route("/courses/<int:course_id>/sessions")
@require_staff
def course_sessions(course_id: int):
    course = get_course_or_404(current_user, course_id, include_archived=True)
    held, total = session_service.round_progress(course)
    return render_template(
        "assistant/course_sessions.html",
        course=course,
        sessions=sessions_for(current_user, course_id=course.id),
        held=held,
        total=total,
    )


@bp.route("/sessions")
@require_staff
def sessions_today():
    """The day's register — what still needs marking."""
    on = _parse_date(request.args.get("date"), date.today())
    return render_template(
        "assistant/sessions_today.html",
        on=on,
        sessions=sessions_for(current_user, on=on),
    )


@bp.route("/sessions/<int:session_id>", methods=["GET"])
@require_staff
def session_detail(session_id: int):
    session = get_session_or_404(current_user, session_id)
    return render_template(
        "assistant/session_detail.html",
        session=session,
        course=session.course,
        statuses=list(AttendanceStatus),
    )


@bp.route("/sessions/<int:session_id>/students/<int:student_id>/mark", methods=["POST"])
@require_staff
def mark_student(session_id: int, student_id: int):
    session = get_session_or_404(current_user, session_id)
    student = get_student_or_404(current_user, student_id)
    status = _parse_status(request.form.get("status"))
    if status is None:
        flash(_("Choose a valid attendance status."), "error")
        return _back(session)

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
    return _back(session)


@bp.route("/sessions/<int:session_id>/teacher/mark", methods=["POST"])
@require_staff
def mark_teacher(session_id: int):
    session = get_session_or_404(current_user, session_id)
    status = _parse_status(request.form.get("status"))
    if status is None:
        flash(_("Choose a valid attendance status."), "error")
        return _back(session)

    try:
        attendance_service.mark_teacher(current_user, session, status)
        flash(_("Teacher attendance recorded."), "success")
    except attendance_service.AttendanceError as exc:
        flash(str(exc), "error")
    return _back(session)


@bp.route("/sessions/<int:session_id>/cancel", methods=["POST"])
@require_staff
def cancel_session(session_id: int):
    session = get_session_or_404(current_user, session_id)
    by_teacher = request.form.get("by") == "teacher"
    try:
        session_service.cancel_session(
            current_user, session, by_teacher=by_teacher, reason=request.form.get("reason")
        )
        flash(_("Session cancelled."), "success")
    except session_service.SessionError as exc:
        flash(str(exc), "error")
    return redirect(url_for("assistant.course_sessions", course_id=session.course_id))


@bp.route("/sessions/<int:session_id>/reschedule", methods=["POST"])
@require_staff
def reschedule_session(session_id: int):
    session = get_session_or_404(current_user, session_id)
    new_date = _parse_date(request.form.get("new_date"))
    raw_time = request.form.get("new_time") or ""
    try:
        hour, minute = raw_time.split(":")[:2]
        new_start = time(hour=int(hour), minute=int(minute))
    except ValueError:
        new_start = None

    if not new_date or new_start is None:
        flash(_("Enter a new date and time."), "error")
        return redirect(url_for("assistant.course_sessions", course_id=session.course_id))

    try:
        session_service.reschedule_session(
            current_user, session, new_date=new_date, new_start=new_start
        )
        flash(_("Session moved."), "success")
    except session_service.SessionError as exc:
        flash(str(exc), "error")
    return redirect(url_for("assistant.course_sessions", course_id=session.course_id))


@bp.route("/attendance")
@require_staff
def attendance_log():
    """Per teacher / per course history with a date range (sprint S3.5)."""
    visible = courses_for(current_user, include_archived=True)
    course_id = request.args.get("course_id", type=int)
    teacher_id = request.args.get("teacher_id", type=int)
    since = _parse_date(request.args.get("since"))
    until = _parse_date(request.args.get("until"))

    course_ids = [c.id for c in visible]
    if course_id and course_id in course_ids:
        course_ids = [course_id]

    return render_template(
        "assistant/attendance_log.html",
        courses=visible,
        teachers=sorted(
            {c.teacher for c in visible if c.teacher}, key=lambda t: t.full_name
        ),
        selected_course_id=course_id,
        selected_teacher_id=teacher_id,
        since=since,
        until=until,
        sessions=attendance_service.history_for_courses(
            course_ids, teacher_id=teacher_id, since=since, until=until
        ),
    )


def _back(session):
    return redirect(url_for("assistant.session_detail", session_id=session.id))


def _in_centre_tz(value: datetime):
    from zoneinfo import ZoneInfo

    from flask import current_app

    return value.astimezone(ZoneInfo(current_app.config["TIMEZONE"]))


@bp.app_template_filter("localtime")
def localtime_filter(value: datetime | None) -> str:
    """Render an aware UTC timestamp in the centre's timezone (PLAN.md §2.3)."""
    if value is None:
        return "—"
    return f"{_in_centre_tz(value):%H:%M}"


@bp.app_template_filter("localdate")
def localdate_filter(value: datetime | None) -> str:
    """Same, as a calendar date — a payment made at 01:00 Cairo is that day."""
    if value is None:
        return "—"
    return f"{_in_centre_tz(value):%Y-%m-%d}"
