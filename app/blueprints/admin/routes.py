"""Admin area: centre overview and assistant accounts (sprint S1.6)."""

from __future__ import annotations

from flask import flash, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user
from sqlalchemy import select

from app.blueprints.admin import bp
from app.blueprints.admin.forms import AssistantForm
from app.decorators import require_role
from app.extensions import db
from app.models.enums import Role
from app.models.user import User
from app.services import accounts as accounts_service
from app.services import dashboard as dashboard_service
from app.services.auth import PhoneNumberError
from app.services.scoping import get_manageable_user_or_404


@bp.route("/")
@require_role(Role.ADMIN)
def overview():
    """Centre-wide view: who is here, what is owed, what still needs marking."""
    return render_template(
        "admin/overview.html",
        dash=dashboard_service.staff_dashboard(current_user),
    )


@bp.route("/assistants", methods=["GET", "POST"])
@require_role(Role.ADMIN)
def assistants():
    """Only the admin creates assistant accounts (brief §Roles)."""
    form = AssistantForm()
    created = None

    if form.validate_on_submit():
        try:
            created = accounts_service.create_assistant(
                current_user, form.full_name.data, form.phone.data, form.resolved_title
            )
            flash(_("Assistant account created."), "success")
            form = AssistantForm(formdata=None)
        except PhoneNumberError:
            flash(_("That phone number is not valid. Use the format 01xxxxxxxxx."), "error")
        except accounts_service.AccountError as exc:
            flash(str(exc), "error")

    existing = db.session.scalars(
        select(User).where(User.role == Role.ASSISTANT).order_by(User.full_name)
    ).all()
    return render_template(
        "admin/assistants.html", form=form, created=created, assistants=existing
    )


@bp.route("/users/<int:user_id>/regenerate-password", methods=["POST"])
@require_role(Role.ADMIN)
def regenerate_password(user_id: int):
    user = get_manageable_user_or_404(current_user, user_id)
    try:
        plaintext = accounts_service.regenerate_password(current_user, user)
    except accounts_service.AccountError as exc:
        flash(str(exc), "error")
        return _back()

    return render_template(
        "admin/credentials.html",
        result=accounts_service.CreationResult(
            accounts=[
                accounts_service.NewAccount(user, plaintext, user.role.value.capitalize())
            ]
        ),
        heading=_("New password issued"),
    )


@bp.route("/users/<int:user_id>/deactivate", methods=["POST"])
@require_role(Role.ADMIN)
def deactivate(user_id: int):
    user = get_manageable_user_or_404(current_user, user_id)
    if user.id == current_user.id:
        flash(_("You cannot deactivate your own account."), "error")
        return _back()
    accounts_service.set_active(current_user, user, False)
    flash(_("Account deactivated."), "success")
    return _back()


def _back():
    from flask import redirect

    return redirect(request.referrer or url_for("admin.overview"))


@bp.route("/audit")
@require_role(Role.ADMIN)
def audit_log():
    """The audit viewer (sprint S8.2).

    Read-only by construction: this blueprint has no route that writes to
    `audit_log`, and `services/audit.py` exposes no update or delete. An audit
    trail an admin can edit is not an audit trail.
    """
    from datetime import date as _date

    from app.services import audit as audit_service

    def parse(raw):
        try:
            return _date.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    actor_id = request.args.get("actor_id", type=int)
    action = request.args.get("action") or None
    entity_type = request.args.get("entity_type") or None
    entity_id = request.args.get("entity_id") or None
    since = parse(request.args.get("since"))
    until = parse(request.args.get("until"))

    return render_template(
        "admin/audit.html",
        entries=audit_service.search(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            since=since,
            until=until,
        ),
        actors=db.session.scalars(
            select(User).where(User.role.in_([Role.ADMIN, Role.ASSISTANT, Role.TEACHER]))
            .order_by(User.full_name)
        ).all(),
        actions=audit_service.known_actions(),
        entity_types=audit_service.known_entity_types(),
        selected={
            "actor_id": actor_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "since": since,
            "until": until,
        },
        diff=audit_service.diff,
    )
