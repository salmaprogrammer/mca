"""Append-only audit trail (sprint S0.6).

Built during the foundation phase, not in P8, so every sprint instruments its
mutations as it goes rather than retrofitting coverage across the whole app.
No route may ever update or delete a row in this table.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import UtcDateTime, utcnow


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(sa.String(60), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(sa.String(60), nullable=False, index=True)
    entity_id: Mapped[str | None] = mapped_column(sa.String(60), index=True)
    before_json: Mapped[dict | None] = mapped_column(sa.JSON)
    after_json: Mapped[dict | None] = mapped_column(sa.JSON)
    ip: Mapped[str | None] = mapped_column(sa.String(45))
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, nullable=False, index=True
    )

    actor = relationship("User", foreign_keys=[actor_id])

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} {self.entity_type}:{self.entity_id}>"
