"""Assistant area: teacher, student and parent accounts (sprint S1.6).

Admin reaches every route here too — admin is treated as a superset of
assistant (flagged assumption, PLAN.md §4).
"""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user
from sqlalchemy import select

from app.blueprints.assistant import bp
from app.blueprints.assistant.forms import StudentForm, TeacherForm
from app.decorators import require_staff
from app.extensions import db
from app.models.enums import Role
from app.models.user import User
from app.services import accounts as accounts_service
from app.services import dashboard as dashboard_service
from app.services.auth import PhoneNumberError
from app.services.scoping import get_manageable_user_or_404, students_for


@bp.route("/")
@require_staff
def home():
    """What needs doing today, not a wall of everything (sprint S7.1)."""
    return render_template(
        "assistant/home.html",
        dash=dashboard_service.staff_dashboard(current_user),
    )


@bp.route("/people/teachers", methods=["GET", "POST"])
@require_staff
def teachers():
    form = TeacherForm()
    created = None

    if form.validate_on_submit():
        try:
            created = accounts_service.create_teacher(
                current_user, form.full_name.data, form.phone.data, form.subject.data
            )
            flash(_("Teacher account created."), "success")
            form = TeacherForm(formdata=None)
        except PhoneNumberError:
            flash(_("That phone number is not valid. Use the format 01xxxxxxxxx."), "error")
        except accounts_service.AccountError as exc:
            flash(str(exc), "error")

    existing = db.session.scalars(
        select(User).where(User.role == Role.TEACHER).order_by(User.full_name)
    ).all()
    return render_template(
        "assistant/teachers.html", form=form, created=created, teachers=existing
    )


@bp.route("/people/students", methods=["GET", "POST"])
@require_staff
def students():
    form = StudentForm()
    created = None

    if form.validate_on_submit():
        try:
            created = accounts_service.create_student_with_parent(
                current_user,
                student_name=form.student_name.data,
                student_phone=form.student_phone.data or None,
                parent_name=form.parent_name.data or None,
                parent_phone=form.parent_phone.data,
                school=form.school.data or None,
                grade=form.grade.data or None,
            )
            flash(_("Student created."), "success")
            form = StudentForm(formdata=None)
        except PhoneNumberError:
            flash(_("That phone number is not valid. Use the format 01xxxxxxxxx."), "error")
        except accounts_service.AccountError as exc:
            flash(str(exc), "error")

    return render_template(
        "assistant/students.html",
        form=form,
        created=created,
        students=students_for(current_user),
    )


@bp.route("/people/<int:user_id>/regenerate-password", methods=["POST"])
@require_staff
def regenerate_password(user_id: int):
    user = get_manageable_user_or_404(current_user, user_id)
    try:
        plaintext = accounts_service.regenerate_password(current_user, user)
    except accounts_service.AccountError as exc:
        flash(str(exc), "error")
        return redirect(request.referrer or url_for("assistant.home"))

    return render_template(
        "admin/credentials.html",
        result=accounts_service.CreationResult(
            accounts=[accounts_service.NewAccount(user, plaintext, user.role.value.capitalize())]
        ),
        heading=_("New password issued"),
    )
