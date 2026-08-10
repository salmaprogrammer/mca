"""Audit recording service (sprint S0.6).

Ground rule 3 (PLAN.md §7): every mutating service call writes an audit row.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from flask import has_request_context, request
from flask_login import current_user

from app.extensions import db
from app.models.audit import AuditLog

# Never lands in an audit payload, whatever the caller passes.
REDACTED_FIELDS = {"password_hash", "password", "plaintext_password", "secret", "token"}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def snapshot(obj: Any, fields: list[str] | None = None) -> dict | None:
    """Capture a model row as a plain dict, minus anything sensitive."""
    if obj is None:
        return None
    if fields is None:
        fields = [c.name for c in obj.__table__.columns]
    return {
        f: _jsonable(getattr(obj, f, None))
        for f in fields
        if f not in REDACTED_FIELDS
    }


def diff(before: dict | None, after: dict | None) -> dict:
    """Only the keys that actually changed — keeps the viewer readable."""
    before, after = before or {}, after or {}
    return {
        k: {"from": before.get(k), "to": after.get(k)}
        for k in set(before) | set(after)
        if before.get(k) != after.get(k)
    }


def record(
    action: str,
    entity_type: str,
    *,
    entity_id: Any = None,
    actor=None,
    before: dict | None = None,
    after: dict | None = None,
    flush: bool = True,
) -> AuditLog:
    """Append one audit row. Caller owns the surrounding transaction."""
    if actor is None and has_request_context() and current_user.is_authenticated:
        actor = current_user

    entry = AuditLog(
        actor_id=getattr(actor, "id", None),
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        before_json=_jsonable(before) if before is not None else None,
        after_json=_jsonable(after) if after is not None else None,
        ip=request.remote_addr if has_request_context() else None,
    )
    db.session.add(entry)
    if flush:
        db.session.flush()
    return entry


# ------------------------------------------------------------- reading


def search(
    *,
    actor_id: int | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    since: date | None = None,
    until: date | None = None,
    limit: int = 200,
) -> list[AuditLog]:
    """Filtered audit history, newest first (sprint S8.2).

    Admin-only at the route; there is no scoped variant because a partial
    audit trail is worse than none — it would let someone conclude a change
    never happened when they simply could not see it.
    """
    from datetime import datetime, time

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    query = select(AuditLog).options(selectinload(AuditLog.actor))

    if actor_id:
        query = query.where(AuditLog.actor_id == actor_id)
    if action:
        query = query.where(AuditLog.action == action)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditLog.entity_id == str(entity_id))
    if since:
        query = query.where(
            AuditLog.created_at >= datetime.combine(since, time.min, tzinfo=timezone.utc)
        )
    if until:
        query = query.where(
            AuditLog.created_at <= datetime.combine(until, time.max, tzinfo=timezone.utc)
        )

    return list(
        db.session.scalars(query.order_by(AuditLog.created_at.desc()).limit(limit))
    )


def known_actions() -> list[str]:
    """Distinct actions actually present, for the filter dropdown."""
    from sqlalchemy import select

    return sorted(
        a for a in db.session.scalars(select(AuditLog.action).distinct()) if a
    )


def known_entity_types() -> list[str]:
    from sqlalchemy import select

    return sorted(
        t for t in db.session.scalars(select(AuditLog.entity_type).distinct()) if t
    )
