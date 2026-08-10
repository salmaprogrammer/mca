"""Response headers and login throttling (sprints S9.1, S9.3).

The Content-Security-Policy is worth reading rather than skimming:

* `script-src 'self'` with **no** `unsafe-inline`. The app has exactly one
  script tag and it loads a vendored file, so the strictest setting costs
  nothing here — and script injection is the attack this actually stops.
* `style-src` does allow `'unsafe-inline'`, because the templates use inline
  `style=` attributes. That is a real (if much smaller) weakening: it permits
  injected styling, not injected code. Removing the inline attributes would
  let this tighten too — worth doing, not worth blocking a release on.
* `frame-ancestors 'none'` and `form-action` are the clickjacking and
  form-hijack halves that `X-Frame-Options` alone does not cover. `form-action`
  lists `https://wa.me` as well as `'self'` because the "send on WhatsApp"
  button posts to this app, which records the hand-off and then redirects to
  the click-to-chat link — and browsers check redirect targets against
  `form-action`, so `'self'` alone silently breaks the button. One fixed host,
  no user data in the path; the message text rides in the query string the
  assistant is about to send anyway.
"""

from __future__ import annotations

from datetime import timedelta

WHATSAPP_LINK_ORIGIN = "https://wa.me"

CSP_DIRECTIVES = {
    "default-src": "'self'",
    "script-src": "'self'",
    "style-src": "'self' 'unsafe-inline'",
    "img-src": "'self' data:",
    "font-src": "'self'",
    # No remote calls at all: everything is vendored (ground rule 6).
    "connect-src": "'self'",
    "form-action": f"'self' {WHATSAPP_LINK_ORIGIN}",
    "frame-ancestors": "'none'",
    "base-uri": "'self'",
    "object-src": "'none'",
}


def content_security_policy() -> str:
    return "; ".join(f"{key} {value}" for key, value in CSP_DIRECTIVES.items())


def register_security_headers(app) -> None:
    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("Content-Security-Policy", content_security_policy())
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        # Nothing here uses a camera, microphone or location.
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )

        # HSTS only where TLS is actually in use; sending it over plain HTTP in
        # development would lock the browser onto https://localhost.
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


# --------------------------------------------------------------- throttle


class LoginThrottle:
    """Per-IP failed-login throttle, counted from the audit log.

    Deliberately no new table and no in-memory counter: failed logins are
    already audited with the source IP, and an in-memory count would reset on
    restart and be per-worker — so several gunicorn workers would each allow
    the full quota. Reading the shared audit table makes the limit real.

    IP-based, not account-based, on purpose: locking an *account* after N
    failures hands anyone a way to lock a teacher out of their own timetable.
    """

    def __init__(self, max_attempts: int, window_minutes: int):
        self.max_attempts = max_attempts
        self.window = timedelta(minutes=window_minutes)

    def recent_failures(self, ip: str | None) -> int:
        if not ip:
            return 0

        from sqlalchemy import func, select

        from app.extensions import db
        from app.models.audit import AuditLog
        from app.models.base import utcnow

        return (
            db.session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.action == "auth.login_failed",
                    AuditLog.ip == ip,
                    AuditLog.created_at >= utcnow() - self.window,
                )
            )
            or 0
        )

    def is_blocked(self, ip: str | None) -> bool:
        return self.recent_failures(ip) >= self.max_attempts


def build_throttle(config) -> LoginThrottle:
    return LoginThrottle(
        max_attempts=config.get("LOGIN_MAX_ATTEMPTS", 10),
        window_minutes=config.get("LOGIN_LOCKOUT_MINUTES", 15),
    )
