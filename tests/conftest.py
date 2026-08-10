"""Shared fixtures (sprint S0.5).

Every later sprint's "done when" is written as a test using these.
"""

from __future__ import annotations

import pytest
from flask import g

from app import create_app
from app.extensions import db as _db
from app.models.enums import Role, TermsAudience
from app.models.terms import TermsAcceptance
from app.models.user import ParentLink, StudentProfile, TeacherProfile, User
from app.seeds.terms_v1 import STUDENT_PARENT_TERMS_AR, TEACHER_TERMS_AR
from app.services import terms as terms_service
from app.services.auth import hash_password

KNOWN_PASSWORD = "Password123"


def _clear_per_request_caches():
    """Drop the per-context caches Flask-Login and Flask-Babel keep on `g`.

    Tests hold one app context open for the whole test so model objects stay
    attached between requests. Flask reuses an already-pushed app context of
    the same app rather than making a fresh one per request, so `g` — and with
    it `g._login_user` and `g.babel_locale` — would otherwise persist across
    every request in a test. That makes the first request's user and language
    stick forever, which silently defeats any test about a *different* user or
    locale.

    Clearing these at the start of each request restores real behaviour: the
    user is re-read from the session cookie and the locale is re-selected.
    Test-harness only; production pushes a fresh context per request anyway.
    """
    g.pop("_login_user", None)  # Flask-Login
    g.pop("_flask_babel", None)  # Flask-Babel 4.x cached locale/timezone
    for key in [k for k in vars(g) if k.startswith("babel_")]:  # Flask-Babel <4
        g.pop(key, None)


@pytest.fixture
def app():
    application = create_app("testing")

    # Must run before the onboarding gates, which read current_user.
    application.before_request_funcs.setdefault(None, []).insert(
        0, _clear_per_request_caches
    )

    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def db(app):
    return _db


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seeded_terms(db):
    """Terms v1 for both audiences, as `flask seed-terms` would publish them."""
    return {
        TermsAudience.TEACHER: terms_service.publish_version(
            None, TermsAudience.TEACHER, body_ar=TEACHER_TERMS_AR
        ),
        TermsAudience.STUDENT_PARENT: terms_service.publish_version(
            None, TermsAudience.STUDENT_PARENT, body_ar=STUDENT_PARENT_TERMS_AR
        ),
    }


def make_user(
    role: Role,
    *,
    name: str | None = None,
    phone: str,
    password: str | None = KNOWN_PASSWORD,
    must_change_password: bool = False,
    is_active: bool = True,
) -> User:
    user = User(
        role=role,
        full_name=name or f"{role.value.title()} User",
        phone=phone,
        username=phone if password else None,
        password_hash=hash_password(password) if password else None,
        must_change_password=must_change_password,
        is_active=is_active,
    )
    _db.session.add(user)
    _db.session.flush()
    if role is Role.TEACHER:
        _db.session.add(TeacherProfile(user_id=user.id, subject="Math"))
    if role is Role.STUDENT:
        _db.session.add(StudentProfile(user_id=user.id))
    _db.session.commit()
    return user


def link_parent(parent: User, student: User) -> ParentLink:
    link = ParentLink(parent_id=parent.id, student_id=student.id)
    _db.session.add(link)
    _db.session.commit()
    return link


def accept_current_terms(user: User) -> None:
    """Push a user past the terms gate without going through the screen."""
    version = terms_service.current_version_for(user)
    if version is None:
        return
    _db.session.add(TermsAcceptance(user_id=user.id, terms_version_id=version.id))
    _db.session.commit()


def login(client, user: User, password: str = KNOWN_PASSWORD):
    return client.post(
        "/login",
        data={"identifier": user.username, "password": password},
        follow_redirects=False,
    )


# --- one ready-to-use user per role -------------------------------------


@pytest.fixture
def admin(db):
    return make_user(Role.ADMIN, phone="+201000000001", name="Center Owner")


@pytest.fixture
def assistant(db):
    return make_user(Role.ASSISTANT, phone="+201000000002", name="Mona Assistant")


@pytest.fixture
def teacher(db, seeded_terms):
    user = make_user(Role.TEACHER, phone="+201011112222", name="Ahmed Fathy")
    accept_current_terms(user)
    return user


@pytest.fixture
def student(db, seeded_terms):
    user = make_user(Role.STUDENT, phone="+201055556666", name="Youssef Adel")
    accept_current_terms(user)
    return user


@pytest.fixture
def parent(db, seeded_terms, student):
    user = make_user(Role.PARENT, phone="+201099990000", name="Adel Mostafa")
    link_parent(user, student)
    accept_current_terms(user)
    return user


# --- logged-in clients ---------------------------------------------------


def _client_for(app, user):
    c = app.test_client()
    login(c, user)
    return c


@pytest.fixture
def as_admin(app, admin):
    return _client_for(app, admin)


@pytest.fixture
def as_assistant(app, assistant):
    return _client_for(app, assistant)


@pytest.fixture
def as_teacher(app, teacher):
    return _client_for(app, teacher)


@pytest.fixture
def as_student(app, student):
    return _client_for(app, student)


@pytest.fixture
def as_parent(app, parent):
    return _client_for(app, parent)


# --- course catalogue helpers (P2) --------------------------------------


@pytest.fixture
def seeded_course_types(db):
    from app.services.courses import seed_course_types

    seed_course_types()
    from app.models.course import CourseType

    return {ct.code: ct for ct in _db.session.scalars(_db.select(CourseType))}


def make_course(
    actor,
    *,
    teacher,
    name="Nov round",
    course_type_code="gpa_course",
    slots=None,
    **kwargs,
):
    """Create a course through the real service, so conflict rules apply."""
    from sqlalchemy import select as _select

    from app.models.course import CourseType
    from app.services import courses as course_service

    course_type = _db.session.scalar(
        _select(CourseType).where(CourseType.code == course_type_code)
    )
    if slots is None:
        slots = [
            {"weekday": 6, "start_time": "16:00"},  # Sunday
            {"weekday": 2, "start_time": "16:00"},  # Wednesday
        ][: course_type.sessions_per_week]

    return course_service.create_course(
        actor,
        name=name,
        course_type_id=course_type.id,
        teacher_id=teacher.id,
        slots=slots,
        **kwargs,
    )
