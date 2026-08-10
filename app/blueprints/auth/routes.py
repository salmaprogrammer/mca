"""Login, logout, and the two onboarding gates (sprints S1.2, S1.3, S1.5)."""

from __future__ import annotations

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_babel import gettext as _
from flask_login import current_user, login_required, login_user, logout_user

from app.blueprints.auth import bp
from app.blueprints.auth.forms import ChangePasswordForm, LoginForm, TermsForm
from app.extensions import db
from app.security import build_throttle
from app.services import audit
from app.services import terms as terms_service
from app.services.auth import authenticate, set_password, verify_password


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    form = LoginForm()
    throttle = build_throttle(current_app.config)

    if form.validate_on_submit():
        # Checked before the password is even looked at, so a locked-out
        # attacker learns nothing from response timing either.
        if throttle.is_blocked(request.remote_addr):
            flash(
                _("Too many failed attempts. Try again in a few minutes."), "error"
            )
            return render_template("auth/login.html", form=form), 429

        user = authenticate(form.identifier.data, form.password.data)
        if user is None:
            # One message for every failure mode. An unknown phone and a wrong
            # password must be indistinguishable (sprint S1.2).
            flash(_("Incorrect phone number or password."), "error")
            audit.record(
                "auth.login_failed",
                "user",
                actor=None,
                after={"identifier_supplied": bool(form.identifier.data)},
            )
            db.session.commit()
            return render_template("auth/login.html", form=form), 401

        login_user(user)
        session.permanent = True
        if user.locale:
            session["locale"] = user.locale
        audit.record("auth.login", "user", entity_id=user.id, actor=user)
        db.session.commit()
        return redirect(url_for("main.index"))

    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    if current_user.is_authenticated:
        audit.record("auth.logout", "user", entity_id=current_user.id, actor=current_user)
        db.session.commit()
    logout_user()
    session.pop("locale", None)
    flash(_("You have been signed out."), "info")
    return redirect(url_for("auth.login"))


@bp.route("/password/change", methods=["GET", "POST"])
@login_required
def change_password():
    """Gate 1. Reachable voluntarily too, so anyone can rotate their password."""
    form = ChangePasswordForm()
    forced = current_user.must_change_password

    if form.validate_on_submit():
        if not verify_password(current_user.password_hash, form.current_password.data):
            flash(_("Your current password is not correct."), "error")
            return render_template("auth/change_password.html", form=form, forced=forced), 400

        if form.new_password.data == form.current_password.data:
            flash(_("Choose a password different from your current one."), "error")
            return render_template("auth/change_password.html", form=form, forced=forced), 400

        set_password(current_user, form.new_password.data, must_change=False)
        audit.record(
            "account.password_changed",
            "user",
            entity_id=current_user.id,
            actor=current_user,
            after={"must_change_password": False},
        )
        db.session.commit()
        flash(_("Your password has been updated."), "success")
        return redirect(url_for("main.index"))

    return render_template("auth/change_password.html", form=form, forced=forced)


@bp.route("/terms", methods=["GET", "POST"])
@login_required
def terms():
    """Gate 2. No dismissal path: accept, or sign out."""
    version = terms_service.current_version_for(current_user)
    if version is None:
        # Admin and assistant have no terms audience.
        return redirect(url_for("main.index"))
    if terms_service.has_accepted(current_user, version):
        return redirect(url_for("main.index"))

    form = TermsForm()
    if form.validate_on_submit():
        terms_service.accept(
            current_user,
            version,
            ip=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
        flash(_("Thank you — your acceptance has been recorded."), "success")
        return redirect(url_for("main.index"))

    if request.method == "POST" and not form.agree.data:
        flash(_("You must agree before continuing."), "error")

    return render_template("auth/terms.html", form=form, version=version)
