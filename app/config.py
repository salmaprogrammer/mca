"""Application configuration.

Nothing here may carry a production-usable default. DevConfig is allowed
convenience defaults; ProdConfig must fail loudly when a secret is missing.
"""

import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"


def _normalise_db_url(url: str) -> str:
    """Heroku/Railway hand out `postgres://`, which SQLAlchemy 2 rejects."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


class Config:
    # --- core ---
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- localisation (§2.3, §0.4) ---
    BABEL_DEFAULT_LOCALE = "ar"
    BABEL_SUPPORTED_LOCALES = ["ar", "en"]
    BABEL_TRANSLATION_DIRECTORIES = str(BASE_DIR / "app" / "translations")
    RTL_LOCALES = {"ar"}
    TIMEZONE = "Africa/Cairo"

    # --- session security ---
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    WTF_CSRF_ENABLED = True

    # --- credentials (§2.4) ---
    GENERATED_PASSWORD_LENGTH = 10
    PHONE_DEFAULT_REGION = "EG"

    # --- uploads ---
    UPLOAD_DIR = INSTANCE_DIR / "uploads"
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024

    # --- centre identity ---
    CENTER_NAME = "MCA Academy"

    # Only enable behind a reverse proxy you control: it makes the app believe
    # X-Forwarded-For, which a direct-to-internet app must never do.
    TRUST_PROXY_HEADERS = False

    # Failed logins per IP before the login form stops accepting attempts.
    LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", 10))
    LOGIN_LOCKOUT_MINUTES = int(os.environ.get("LOGIN_LOCKOUT_MINUTES", 15))

    # --- WhatsApp -----------------------------------------------------
    # There is no API integration. The app composes each daily update and the
    # assistant sends it from their own WhatsApp via a click-to-chat link, so
    # there is nothing to configure and no credential to leak. The base of that
    # link is config only so a future move to wa.me alternatives is one line.
    WHATSAPP_LINK_BASE = os.environ.get("WHATSAPP_LINK_BASE", "https://wa.me")

    # ------------------------------------------------------------------
    # OPEN QUESTION 1 (PLAN.md §8) — student and parent sharing one phone.
    # Recommendation (b) is implemented: when only one phone is available,
    # create the parent login and give the student no login of their own.
    # Change this flag to switch behaviour once the owner decides.
    #   "parent_only"     -> (b) recommended, implemented
    #   "require_distinct"-> (a) refuse creation unless the phones differ
    # ------------------------------------------------------------------
    FAMILY_SHARED_PHONE_MODE = os.environ.get("FAMILY_SHARED_PHONE_MODE", "parent_only")

    # OPEN QUESTION 4 — default session length, used from P2 onward.
    DEFAULT_SESSION_DURATION_MINUTES = 90


class DevConfig(Config):
    DEBUG = True
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-for-production")
    SQLALCHEMY_DATABASE_URI = _normalise_db_url(
        os.environ.get("DATABASE_URL", f"sqlite:///{INSTANCE_DIR / 'mca-dev.sqlite'}")
    )


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"
    # In-memory SQLite by default. Set TEST_DATABASE_URL to run the whole
    # suite against a throwaway Postgres instead — see scripts/verify-postgres.sh.
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL", "sqlite://")
    WTF_CSRF_ENABLED = False  # forms are exercised directly; CSRF has its own test
    GENERATED_PASSWORD_LENGTH = 10


class ProdConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"
    TRUST_PROXY_HEADERS = True

    def __init__(self) -> None:
        missing = [k for k in ("SECRET_KEY", "DATABASE_URL") if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
        self.SECRET_KEY = os.environ["SECRET_KEY"]
        self.SQLALCHEMY_DATABASE_URI = _normalise_db_url(os.environ["DATABASE_URL"])


CONFIGS = {"development": DevConfig, "testing": TestConfig, "production": ProdConfig}


def get_config(name: str | None = None):
    name = name or os.environ.get("FLASK_CONFIG", "development")
    cfg = CONFIGS[name]
    return cfg() if name == "production" else cfg
