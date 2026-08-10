"""Locale selection and RTL handling (sprint S0.4)."""

from __future__ import annotations

from flask import current_app, has_request_context, request, session
from flask_login import current_user


def supported_locales() -> list[str]:
    return current_app.config["BABEL_SUPPORTED_LOCALES"]


def select_locale() -> str:
    """Session override → saved user preference → Accept-Language → default.

    Must survive being called with no request in flight: CLI commands and
    scheduled jobs translate user-facing text too (conflict messages, the
    daily WhatsApp summary), and reaching for `session` there would crash.
    """
    supported = supported_locales()
    default = current_app.config["BABEL_DEFAULT_LOCALE"]

    if not has_request_context():
        return default

    chosen = session.get("locale")
    if chosen in supported:
        return chosen

    if current_user.is_authenticated and current_user.locale in supported:
        return current_user.locale

    return request.accept_languages.best_match(supported) or default


def is_rtl(locale: str | None = None) -> bool:
    from flask_babel import get_locale

    locale = locale or str(get_locale() or current_app.config["BABEL_DEFAULT_LOCALE"])
    return locale in current_app.config["RTL_LOCALES"]


def text_direction(locale: str | None = None) -> str:
    return "rtl" if is_rtl(locale) else "ltr"
