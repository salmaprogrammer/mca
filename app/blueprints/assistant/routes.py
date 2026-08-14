"""Assistant area: teacher, student and parent accounts (sprint S1.6).

Admin reaches every route here too — admin is treated as a superset of
assistant (flagged assumption, PLAN.md §4).
"""

from __future__ import annotations

from flask import flash, redirect, render_template, request
from flask import session as flask_session
from flask import url_for
from flask_babel import gettext as _
from flask_login import current_user
from sqlalchemy import select

from app.blueprints.assistant import bp
from app.blueprints.assistant.forms import (
    ParentEditForm,
    StudentEditForm,
    StudentForm,
    TeacherEditForm,
    TeacherForm,
)
from app.decorators import require_staff
from app.extensions import db
from app.models.enums import Role
from app.models.user import User
from app.services import accounts as accounts_service
from app.services import dashboard as dashboard_service
from app.services.auth import PhoneNumberError, UsernameError
from app.services.scoping import get_manageable_user_or_404, students_for

# One-time credentials survive a single redirect via the signed session cookie:
# the POST handler stashes them here, the redirected GET pops and renders them.
# The point of the redirect is that a browser refresh cannot replay the POST
# and create a duplicate account. The plaintext lives in the cookie for one
# request pair only; it was already about to be displayed on-screen anyway.
_CREDENTIAL_STASH_KEY = "_pending_credentials"


def _stash_credentials(result: accounts_service.CreationResult | None) -> None:
    if not result or (not result.accounts and not result.notices):
        return
    flask_session[_CREDENTIAL_STASH_KEY] = {
        "accounts": [
            {
                "user_id": account.user.id,
                "plaintext": account.plaintext_password,
                "label": account.label,
            }
            for account in result.accounts
        ],
        "notices": list(result.notices),
    }


def _pop_credentials() -> accounts_service.CreationResult | None:
    data = flask_session.pop(_CREDENTIAL_STASH_KEY, None)
    if not data:
        return None
    accounts: list[accounts_service.NewAccount] = []
    for row in data.get("accounts", []):
        user = db.session.get(User, row["user_id"])
        if user is None:
            continue
        accounts.append(
            accounts_service.NewAccount(user, row.get("plaintext"), row.get("label", ""))
        )
    return accounts_service.CreationResult(accounts=accounts, notices=data.get("notices", []))


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

    if form.validate_on_submit():
        try:
            result = accounts_service.create_teacher(
                current_user, form.full_name.data, form.phone.data, form.subject.data
            )
        except PhoneNumberError:
            flash(_("That phone number is not valid. Use the format 01xxxxxxxxx."), "error")
        except accounts_service.AccountError as exc:
            flash(str(exc), "error")
        else:
            _stash_credentials(result)
            flash(_("Teacher account created."), "success")
            return redirect(url_for("assistant.teachers"))

    existing = db.session.scalars(
        select(User).where(User.role == Role.TEACHER).order_by(User.full_name)
    ).all()
    return render_template(
        "assistant/teachers.html",
        form=form,
        created=_pop_credentials(),
        teachers=existing,
    )


@bp.route("/people/students", methods=["GET", "POST"])
@require_staff
def students():
    form = StudentForm()

    if form.validate_on_submit():
        try:
            result = accounts_service.create_student_with_parent(
                current_user,
                student_name=form.student_name.data,
                student_phone=form.student_phone.data or None,
                parent_name=form.parent_name.data or None,
                parent_phone=form.parent_phone.data,
                school=form.school.data or None,
                grade=form.grade.data or None,
            )
        except PhoneNumberError:
            flash(_("That phone number is not valid. Use the format 01xxxxxxxxx."), "error")
        except accounts_service.AccountError as exc:
            flash(str(exc), "error")
        else:
            _stash_credentials(result)
            flash(_("Student created."), "success")
            return redirect(url_for("assistant.students"))

    return render_template(
        "assistant/students.html",
        form=form,
        created=_pop_credentials(),
        students=students_for(current_user),
    )


@bp.route("/people/<int:user_id>/edit", methods=["GET", "POST"])
@require_staff
def person_edit(user_id: int):
    """Edit an existing student/teacher/parent's profile.

    The whole reason this exists: fixing typos in a fresh account instead of
    creating a duplicate. Route added under the assistant blueprint because
    that is the role that actually types the data in.
    """
    user = get_manageable_user_or_404(current_user, user_id)

    if user.role is Role.TEACHER:
        form = TeacherEditForm(obj=None)
        if request.method == "GET":
            form.full_name.data = user.full_name
            form.phone.data = user.phone
            form.username.data = (
                user.username if user.username and user.username != user.phone else ""
            )
            form.subject.data = user.teacher_profile.subject if user.teacher_profile else ""
    elif user.role is Role.STUDENT:
        form = StudentEditForm(obj=None)
        if request.method == "GET":
            form.full_name.data = user.full_name
            form.phone.data = user.phone
            form.username.data = (
                user.username if user.username and user.username != user.phone else ""
            )
            form.school.data = user.student_profile.school if user.student_profile else ""
            form.grade.data = user.student_profile.grade if user.student_profile else ""
            existing_parent = user.parents[0] if user.parents else None
            form.parent_name.data = existing_parent.full_name if existing_parent else ""
            form.parent_phone.data = existing_parent.phone if existing_parent else ""
    elif user.role is Role.PARENT:
        form = ParentEditForm(obj=None)
        if request.method == "GET":
            form.full_name.data = user.full_name
            form.phone.data = user.phone
            form.username.data = (
                user.username if user.username and user.username != user.phone else ""
            )
    else:
        # Admin/assistant accounts have their own admin-side flow.
        flash(_("This account type is edited elsewhere."), "error")
        return redirect(url_for("assistant.home"))

    if form.validate_on_submit():
        fields = {"full_name": form.full_name.data, "phone": form.phone.data,
                  "username": form.username.data}
        if user.role is Role.TEACHER:
            fields["subject"] = form.subject.data
        elif user.role is Role.STUDENT:
            fields["school"] = form.school.data
            fields["grade"] = form.grade.data

        try:
            accounts_service.update_user_profile(current_user, user, **fields)
            if user.role is Role.STUDENT and form.parent_phone.data:
                parent, plaintext = accounts_service.set_student_parent_phone(
                    current_user, user, form.parent_phone.data, form.parent_name.data
                )
                if plaintext:
                    _stash_credentials(
                        accounts_service.CreationResult(
                            accounts=[
                                accounts_service.NewAccount(parent, plaintext, "Parent")
                            ]
                        )
                    )
        except PhoneNumberError:
            flash(_("That phone number is not valid. Use the format 01xxxxxxxxx."), "error")
        except UsernameError as exc:
            flash(str(exc), "error")
        except accounts_service.AccountError as exc:
            flash(str(exc), "error")
        else:
            flash(_("Changes saved."), "success")
            if user.role is Role.TEACHER:
                return redirect(url_for("assistant.teachers"))
            return redirect(url_for("assistant.students"))

    return render_template("assistant/person_edit.html", form=form, user=user)


@bp.route("/people/<int:user_id>/delete", methods=["POST"])
@require_staff
def person_delete(user_id: int):
    """Hard-delete when there is no history; deactivate otherwise.

    The fall-back to deactivate is deliberate — a real student with attendance
    or enrollments must never disappear from the trail. Only fresh duplicates
    (no linked rows at all) actually get removed.
    """
    user = get_manageable_user_or_404(current_user, user_id)
    if user.role not in (Role.STUDENT, Role.TEACHER, Role.PARENT):
        flash(_("This account type cannot be deleted here."), "error")
        return redirect(url_for("assistant.home"))

    try:
        outcome = accounts_service.delete_user_safely(current_user, user)
    except accounts_service.AccountError as exc:
        flash(str(exc), "error")
        return redirect(request.referrer or url_for("assistant.home"))

    if outcome == "deleted":
        flash(_("Account deleted."), "success")
    else:
        flash(
            _("This account has history and was deactivated instead of deleted."),
            "success",
        )
    if user.role is Role.TEACHER:
        return redirect(url_for("assistant.teachers"))
    return redirect(url_for("assistant.students"))


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
