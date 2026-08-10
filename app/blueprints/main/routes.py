"""Health check, role routing, and the language toggle (sprints S0.1, S0.4)."""

from __future__ import annotations

from flask import redirect, request, session, url_for
from flask_login import current_user, login_required

from app.blueprints.main import bp
from app.extensions import db
from app.models.enums import Role

ROLE_HOME = {
    Role.ADMIN: "admin.overview",
    Role.ASSISTANT: "assistant.home",
    Role.TEACHER: "teacher.home",
    Role.STUDENT: "portal.home",
    Role.PARENT: "portal.home",
}


@bp.route("/healthz")
def healthz():
    return {"status": "ok"}


@bp.route("/")
@login_required
def index():
    return redirect(url_for(ROLE_HOME[current_user.role]))


@bp.route("/me/language", methods=["POST"])
def set_language():
    locale = request.form.get("locale", "")
    from flask import current_app

    if locale in current_app.config["BABEL_SUPPORTED_LOCALES"]:
        session["locale"] = locale
        if current_user.is_authenticated:
            current_user.locale = locale
            db.session.commit()

    target = request.form.get("next") or request.referrer or url_for("main.index")
    return redirect(_safe_redirect(target))


def _safe_redirect(target: str) -> str:
    """Only ever redirect within this app (open-redirect guard, S9.3)."""
    from urllib.parse import urlparse

    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return url_for("main.index")
    return target or url_for("main.index")
