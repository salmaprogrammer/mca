"""Application factory (sprint S0.1)."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, redirect, render_template, url_for
from flask_babel import lazy_gettext as _l
from flask_login import current_user

from app.config import get_config
from app.extensions import babel, csrf, db, login_manager, migrate
from app.i18n import is_rtl, text_direction


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(get_config(config_name))

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)

    _init_extensions(app)
    _register_blueprints(app)
    _register_gates(app)
    _register_template_globals(app)
    _register_security(app)
    _register_error_handlers(app)
    _register_cli(app)

    return app


def _init_extensions(app: Flask) -> None:
    from app.i18n import select_locale

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    babel.init_app(app, locale_selector=select_locale)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    # Flask-Login's default message is untranslated English and would appear
    # verbatim in the Arabic UI.
    login_manager.login_message = _l("Please sign in to continue.")
    login_manager.login_message_category = "info"
    # "basic", not "strong". Strong protection destroys the session whenever the
    # client IP or user agent changes; parents here are on Egyptian mobile data
    # and hop between wifi and 4G, which would sign them out mid-visit for no
    # real gain at this data sensitivity.
    login_manager.session_protection = "basic"

    @login_manager.user_loader
    def load_user(user_id: str):
        from app.models.user import User

        return db.session.get(User, int(user_id))

    # Import models so Alembic sees every table.
    from app import models  # noqa: F401


def _register_blueprints(app: Flask) -> None:
    from app.blueprints.admin import bp as admin_bp
    from app.blueprints.assistant import bp as assistant_bp
    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.main import bp as main_bp
    from app.blueprints.portal import bp as portal_bp
    from app.blueprints.teacher import bp as teacher_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(assistant_bp, url_prefix="/assistant")
    app.register_blueprint(teacher_bp, url_prefix="/teacher")
    app.register_blueprint(portal_bp, url_prefix="/portal")


def _register_gates(app: Flask) -> None:
    from app.gates import register_gates

    register_gates(app)


def _register_template_globals(app: Flask) -> None:
    from flask_babel import get_locale

    from app.formatting import format_time_12h
    from app.labels import label_for
    from app.models.course import weekday_label
    from app.models.enums import Role

    # Jinja *globals*, not a context processor: macros imported with
    # `{% from ... import x %}` get no template context, so anything a shared
    # macro needs has to live on the environment or it is silently undefined.
    app.jinja_env.globals.update(
        get_locale=get_locale,
        weekday_label=weekday_label,
        label_for=label_for,
        is_rtl=is_rtl,
        text_direction=text_direction,
        Role=Role,
        center_name=app.config["CENTER_NAME"],
        supported_locales=app.config["BABEL_SUPPORTED_LOCALES"],
    )
    app.jinja_env.filters["time12"] = format_time_12h


def _register_security(app: Flask) -> None:
    from app.security import register_security_headers

    register_security_headers(app)

    # Behind nginx every request otherwise appears to come from 127.0.0.1,
    # which would make the per-IP login throttle throttle *everyone* at once
    # and fill the audit log with the proxy's address.
    if app.config.get("TRUST_PROXY_HEADERS"):
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(401)
    def unauthorised(_error):
        if current_user.is_authenticated:
            return render_template("errors/403.html"), 403
        return redirect(url_for("auth.login"))

    @app.errorhandler(500)
    def server_error(_error):
        db.session.rollback()
        return render_template("errors/500.html"), 500


def _register_cli(app: Flask) -> None:
    from app.cli import register_cli

    register_cli(app)
