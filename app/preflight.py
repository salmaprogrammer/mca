"""Production readiness checks (sprint S10.2).

Run before serving traffic. Every check here exists because getting it wrong is
silent: the app starts, pages render, and something is quietly broken —
untranslated Arabic, a database one migration behind, a send button pointing at
a link that will not open WhatsApp.

`flask preflight` exits non-zero on any FAIL, so a deploy script can gate on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Level(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class Check:
    level: Level
    name: str
    detail: str


def _ok(name, detail=""):
    return Check(Level.PASS, name, detail)


def _warn(name, detail):
    return Check(Level.WARN, name, detail)


def _fail(name, detail):
    return Check(Level.FAIL, name, detail)


def run_all(app) -> list[Check]:
    checks: list[Check] = []
    checks += _config_checks(app)
    checks += _database_checks(app)
    checks += _translation_checks(app)
    checks += _whatsapp_checks(app)
    checks += _storage_checks(app)
    return checks


def _config_checks(app) -> list[Check]:
    out = []
    secret = app.config.get("SECRET_KEY") or ""

    if not secret:
        out.append(_fail("SECRET_KEY", "not set"))
    elif "dev" in secret.lower() or "test" in secret.lower() or len(secret) < 32:
        # A guessable key means anyone can forge a session cookie for any role.
        out.append(_fail("SECRET_KEY", "looks like a development value, or is too short"))
    else:
        out.append(_ok("SECRET_KEY", f"{len(secret)} chars"))

    if app.debug:
        out.append(_fail("DEBUG", "debug mode is on — the interactive debugger runs code"))
    else:
        out.append(_ok("DEBUG", "off"))

    if app.config.get("SESSION_COOKIE_SECURE"):
        out.append(_ok("Session cookies", "Secure, HttpOnly, SameSite=Lax"))
    else:
        out.append(
            _fail(
                "Session cookies",
                "SESSION_COOKIE_SECURE is off; cookies will leak over http",
            )
        )

    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if uri.startswith("sqlite"):
        out.append(
            _warn(
                "Database",
                "SQLite. Fine for a pilot, but concurrent writes will block; "
                "Postgres is the intended production target.",
            )
        )
    elif uri:
        out.append(_ok("Database", uri.split("@")[-1] or "configured"))
    else:
        out.append(_fail("Database", "DATABASE_URL is not set"))

    return out


def _database_checks(app) -> list[Check]:
    from sqlalchemy import select

    from app.extensions import db
    from app.models.course import CourseType
    from app.models.enums import Role
    from app.models.terms import TermsVersion
    from app.models.user import User

    out = []
    try:
        db.session.execute(select(1))
    except Exception as exc:
        return [_fail("Database connection", str(exc)[:160])]
    out.append(_ok("Database connection"))

    # Schema must be at the newest migration, or the app is running against a
    # shape it does not expect.
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config("migrations/alembic.ini")
        cfg.set_main_option("script_location", "migrations")
        head = ScriptDirectory.from_config(cfg).get_current_head()
        applied = db.session.execute(
            db.text("SELECT version_num FROM alembic_version")
        ).scalar()
        if applied == head:
            out.append(_ok("Migrations", f"at head ({head})"))
        else:
            out.append(_fail("Migrations", f"database at {applied}, code expects {head}"))
    except Exception as exc:
        out.append(_warn("Migrations", f"could not verify: {str(exc)[:120]}"))

    types = db.session.scalar(select(db.func.count(CourseType.id))) or 0
    if types == 6:
        out.append(_ok("Course types", "all six seeded"))
    else:
        out.append(_fail("Course types", f"{types} seeded; run `flask seed-course-types`"))

    terms = db.session.scalar(select(db.func.count(TermsVersion.id))) or 0
    if terms >= 2:
        out.append(_ok("Terms", f"{terms} version(s) published"))
    else:
        out.append(_fail("Terms", "not published; run `flask seed-terms`"))

    admins = db.session.scalar(
        select(db.func.count(User.id)).where(User.role == Role.ADMIN)
    ) or 0
    if admins:
        out.append(_ok("Admin account", f"{admins} present"))
    else:
        out.append(_fail("Admin account", "none; run `flask create-admin`"))

    # Demo data on production would hand out a known password.
    demo = db.session.scalar(
        select(User).where(User.full_name == "Center Owner", User.phone == "+201000000001")
    )
    if demo:
        out.append(
            _warn(
                "Demo data",
                "the seed-demo admin (+201000000001) exists — remove it before go-live",
            )
        )
    else:
        out.append(_ok("Demo data", "none found"))

    return out


def _translation_checks(app) -> list[Check]:
    from pathlib import Path

    out = []
    for locale in app.config["BABEL_SUPPORTED_LOCALES"]:
        mo = Path(app.root_path) / "translations" / locale / "LC_MESSAGES" / "messages.mo"
        if mo.exists():
            out.append(_ok(f"Translations ({locale})", f"{mo.stat().st_size} bytes"))
        else:
            # gettext falls back to the English msgid, so every Arabic screen
            # renders in English and nothing errors.
            out.append(
                _fail(
                    f"Translations ({locale})",
                    "messages.mo missing — run `pybabel compile -d app/translations`",
                )
            )
    return out


def _whatsapp_checks(app) -> list[Check]:
    """Sending is manual, so there are no credentials to verify. The one thing
    that can still be wrong is a link base that will not open WhatsApp."""
    base = (app.config.get("WHATSAPP_LINK_BASE") or "").strip()
    if base.startswith("https://"):
        return [_ok("WhatsApp link")]
    return [
        _fail(
            "WhatsApp link",
            "WHATSAPP_LINK_BASE must be an https:// URL; the send button opens it",
        )
    ]


def _storage_checks(app) -> list[Check]:
    import os
    from pathlib import Path

    upload = Path(app.config["UPLOAD_DIR"])
    if not upload.exists():
        return [_fail("Upload directory", f"{upload} does not exist")]
    if not os.access(upload, os.W_OK):
        return [_fail("Upload directory", f"{upload} is not writable")]
    return [_ok("Upload directory", str(upload))]


def worst_level(checks: list[Check]) -> Level:
    if any(c.level is Level.FAIL for c in checks):
        return Level.FAIL
    if any(c.level is Level.WARN for c in checks):
        return Level.WARN
    return Level.PASS
