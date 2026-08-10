"""Onboarding gates enforced server-side (sprints S1.3, S1.5).

Two walls, in order: forced password change, then terms acceptance. Both live
in a `before_request` hook rather than in templates, so typing a dashboard URL
directly cannot walk around them.
"""

from __future__ import annotations

from flask import redirect, request, url_for
from flask_login import current_user

from app.services import terms as terms_service

# Reachable while a gate is up. Logging out is always allowed — the brief
# requires the terms screen to offer no exit except accepting or leaving.
GATE_EXEMPT_ENDPOINTS = {
    "static",
    "auth.login",
    "auth.logout",
    "auth.change_password",
    "auth.terms",
    "main.healthz",
    "main.set_language",
}


def register_gates(app) -> None:
    @app.before_request
    def enforce_onboarding_gates():
        if request.endpoint in GATE_EXEMPT_ENDPOINTS or request.endpoint is None:
            return None
        if not current_user.is_authenticated:
            return None

        # 1. Password first: a generated password must be replaced before the
        #    user is asked to agree to anything.
        if current_user.must_change_password and request.endpoint != "auth.change_password":
            return redirect(url_for("auth.change_password"))

        # 2. Then terms. Admin and assistant are never held here.
        if terms_service.needs_acceptance(current_user):
            return redirect(url_for("auth.terms"))

        return None
