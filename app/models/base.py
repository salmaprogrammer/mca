"""Shared model mixins and the UTC timestamp column type.

Event timestamps are timezone-aware UTC (PLAN.md §2.3). Wall-clock session
times, added in P3, are deliberately *naive* local time and must not use these.
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator):
    """A timestamp that is always timezone-aware UTC, on every backend.

    Postgres `TIMESTAMPTZ` round-trips tz-aware values; SQLite has no timezone
    type at all and hands back naive datetimes. Without this, the same column
    behaves differently in dev and production, and anything calling
    `.astimezone()` on the naive result silently assumes the *server's* local
    zone — which is how attendance times end up hours out.

    Values are normalised to UTC on the way in and re-tagged as UTC on the way
    out, so callers can rely on `.tzinfo` being set.
    """

    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            # A naive value reaching here is a bug upstream; assume UTC rather
            # than the server's zone, which is at least deterministic.
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
