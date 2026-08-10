"""Versioned terms & conditions and per-user acceptance records (sprint S1.4).

The gate asks "is there an acceptance row for the *current* version for my
audience". It is never a boolean on the user — that is what lets the business
publish v2 later and have everyone re-prompted automatically (brief §Onboarding).
"""

from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UtcDateTime, utcnow
from app.models.enums import TermsAudience, enum_column


class TermsVersion(TimestampMixin, db.Model):
    __tablename__ = "terms_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    audience: Mapped[TermsAudience] = mapped_column(
        enum_column(TermsAudience, 20), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(nullable=False)
    body_ar: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # OPEN QUESTION 6 — English text not yet supplied; falls back to Arabic.
    body_en: Mapped[str | None] = mapped_column(sa.Text)
    effective_from: Mapped[date] = mapped_column(sa.Date, default=date.today, nullable=False)
    is_current: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)

    acceptances = relationship("TermsAcceptance", back_populates="version")

    __table_args__ = (
        sa.UniqueConstraint("audience", "version", name="uq_terms_versions_audience_version"),
    )

    def body_for(self, locale: str) -> str:
        if locale == "en" and self.body_en:
            return self.body_en
        return self.body_ar

    def __repr__(self) -> str:
        return f"<TermsVersion {self.audience.value} v{self.version}>"


class TermsAcceptance(db.Model):
    __tablename__ = "terms_acceptances"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id"), nullable=False, index=True)
    terms_version_id: Mapped[int] = mapped_column(
        sa.ForeignKey("terms_versions.id"), nullable=False, index=True
    )
    accepted_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, nullable=False
    )
    ip: Mapped[str | None] = mapped_column(sa.String(45))
    user_agent: Mapped[str | None] = mapped_column(sa.String(255))

    user = relationship("User")
    version = relationship("TermsVersion", back_populates="acceptances")

    __table_args__ = (
        sa.UniqueConstraint("user_id", "terms_version_id", name="uq_terms_acceptance_once"),
    )
