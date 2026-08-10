"""Role gating for whole routes (sprint S1.7)."""

from __future__ import annotations

from functools import wraps

from flask import abort, current_app
from flask_login import current_user

from app.models.enums import Role

STAFF = (Role.ADMIN, Role.ASSISTANT)


def require_role(*roles: Role):
    """Restrict a route to the given roles.

    403, not 404: the role-gated URLs are fixed and publicly known, so hiding
    their existence buys nothing. Row-level scoping in services/scoping.py is
    where 404 matters, because there the ID itself is the secret.
    """

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                # Hand off to Flask-Login rather than aborting, so anonymous
                # users get the same translated prompt and ?next= handling as
                # @login_required gives them.
                return current_app.login_manager.unauthorized()
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def require_staff(view):
    """Admin or assistant. Admin is treated as a superset of assistant.

    Flagged assumption (PLAN.md §4): the brief grants admin "full read/write
    access to all data" but its own open-questions list asks whether admin can
    act as an assistant. Narrowing later is easy; widening later is not.
    """
    return require_role(*STAFF)(view)
