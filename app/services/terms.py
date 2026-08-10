"""Terms & conditions gating and versioning (sprints S1.4, S1.5)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.extensions import db
from app.models.enums import TermsAudience
from app.models.terms import TermsAcceptance, TermsVersion
from app.models.user import User
from app.services import audit


def current_version_for(user: User) -> TermsVersion | None:
    """The live terms for this user's audience, or None for admin/assistant."""
    audience = TermsAudience.for_role(user.role)
    if audience is None:
        return None
    return db.session.scalar(
        select(TermsVersion)
        .where(TermsVersion.audience == audience, TermsVersion.is_current.is_(True))
        .order_by(TermsVersion.version.desc())
    )


def has_accepted(user: User, version: TermsVersion) -> bool:
    return (
        db.session.scalar(
            select(TermsAcceptance.id).where(
                TermsAcceptance.user_id == user.id,
                TermsAcceptance.terms_version_id == version.id,
            )
        )
        is not None
    )


def needs_acceptance(user: User) -> bool:
    """True when this user must be held at the terms screen.

    Deliberately a *query for an acceptance row against the current version*,
    never a boolean on the user — that is what makes publishing v2 re-prompt
    everyone automatically instead of needing a migration.
    """
    version = current_version_for(user)
    if version is None:
        return False
    return not has_accepted(user, version)


def accept(
    user: User, version: TermsVersion, *, ip: str | None = None, user_agent: str | None = None
) -> TermsAcceptance:
    existing = db.session.scalar(
        select(TermsAcceptance).where(
            TermsAcceptance.user_id == user.id,
            TermsAcceptance.terms_version_id == version.id,
        )
    )
    if existing:
        return existing

    acceptance = TermsAcceptance(
        user_id=user.id,
        terms_version_id=version.id,
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
    )
    db.session.add(acceptance)
    db.session.flush()
    audit.record(
        "terms.accepted",
        "terms_acceptance",
        entity_id=acceptance.id,
        actor=user,
        after={
            "user_id": user.id,
            "audience": version.audience.value,
            "version": version.version,
        },
    )
    db.session.commit()
    return acceptance


def publish_version(
    actor: User | None,
    audience: TermsAudience,
    *,
    body_ar: str,
    body_en: str | None = None,
    effective_from: date | None = None,
) -> TermsVersion:
    """Publish the next version and retire the previous one.

    Everyone in the audience is re-prompted on their next request, which is the
    mechanism the brief asked for when terms change.
    """
    latest = db.session.scalar(
        select(TermsVersion)
        .where(TermsVersion.audience == audience)
        .order_by(TermsVersion.version.desc())
    )
    next_number = (latest.version + 1) if latest else 1

    db.session.execute(
        TermsVersion.__table__.update()
        .where(TermsVersion.audience == audience)
        .values(is_current=False)
    )

    version = TermsVersion(
        audience=audience,
        version=next_number,
        body_ar=body_ar,
        body_en=body_en,
        effective_from=effective_from or date.today(),
        is_current=True,
    )
    db.session.add(version)
    db.session.flush()
    audit.record(
        "terms.published",
        "terms_version",
        entity_id=version.id,
        actor=actor,
        after={"audience": audience.value, "version": next_number},
    )
    db.session.commit()
    return version
