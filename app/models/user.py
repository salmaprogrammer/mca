"""Users, role profiles, and parent/student links (sprint S1.1)."""

from __future__ import annotations

import sqlalchemy as sa
from flask_login import UserMixin
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin
from app.models.enums import Role, enum_column


class User(UserMixin, TimestampMixin, db.Model):
    """One row per person, whatever their role.

    `phone`, `username` and `password_hash` are nullable on purpose: a student
    in a one-phone family has no login of their own and is reached through
    their parent's account (OPEN QUESTION 1, config FAMILY_SHARED_PHONE_MODE).
    Such a row is a real student with attendance and feedback — it simply
    cannot authenticate.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[Role] = mapped_column(enum_column(Role, 16), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(sa.String(160), nullable=False)

    # E.164, normalised on write by services.auth.normalise_phone
    phone: Mapped[str | None] = mapped_column(sa.String(24), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(sa.String(24), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(sa.String(255))

    must_change_password: Mapped[bool] = mapped_column(default=True, nullable=False)
    locale: Mapped[str] = mapped_column(sa.String(5), default="ar", nullable=False)
    # Shadows UserMixin.is_active on purpose so Flask-Login refuses deactivated staff.
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))
    created_by = relationship("User", remote_side=[id], foreign_keys=[created_by_id])

    teacher_profile = relationship(
        "TeacherProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    student_profile = relationship(
        "StudentProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    assistant_profile = relationship(
        "AssistantProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    children_links = relationship(
        "ParentLink",
        foreign_keys="ParentLink.parent_id",
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    parent_links = relationship(
        "ParentLink",
        foreign_keys="ParentLink.student_id",
        back_populates="student",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        sa.CheckConstraint(
            "(username IS NULL) OR (password_hash IS NOT NULL)",
            name="ck_users_login_needs_password",
        ),
    )

    # --- convenience -------------------------------------------------

    @property
    def can_log_in(self) -> bool:
        return bool(self.username and self.password_hash and self.is_active)

    @property
    def children(self) -> list[User]:
        return [link.student for link in self.children_links]

    @property
    def parents(self) -> list[User]:
        return [link.parent for link in self.parent_links]

    def has_role(self, *roles: Role) -> bool:
        return self.role in roles

    def __repr__(self) -> str:  # never include phone or hash
        return f"<User {self.id} {self.role.value}>"


class TeacherProfile(db.Model):
    __tablename__ = "teacher_profiles"

    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id"), primary_key=True)
    subject: Mapped[str | None] = mapped_column(sa.String(80))
    # 60/40 split from the teacher terms; payout screens are P-deferred (open question 9).
    payout_share: Mapped[float] = mapped_column(sa.Numeric(4, 3), default=0.600, nullable=False)
    notes: Mapped[str | None] = mapped_column(sa.Text)

    user = relationship("User", back_populates="teacher_profile")


class StudentProfile(db.Model):
    __tablename__ = "student_profiles"

    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id"), primary_key=True)
    school: Mapped[str | None] = mapped_column(sa.String(120))
    grade: Mapped[str | None] = mapped_column(sa.String(40))
    notes: Mapped[str | None] = mapped_column(sa.Text)

    user = relationship("User", back_populates="student_profile")


class AssistantProfile(db.Model):
    """A display-only job title for an assistant account.

    Purely cosmetic: this never changes what the account can do — that is
    still governed entirely by `Role.ASSISTANT`. A blank/absent title falls
    back to the generic "Assistant" label wherever the role is shown.
    """

    __tablename__ = "assistant_profiles"

    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id"), primary_key=True)
    title: Mapped[str | None] = mapped_column(sa.String(80))

    user = relationship("User", back_populates="assistant_profile")


class ParentLink(db.Model):
    """Many-to-many: one parent may have several children, one child several parents."""

    __tablename__ = "parent_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id"), nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id"), nullable=False, index=True)
    relation: Mapped[str | None] = mapped_column(sa.String(40))

    parent = relationship("User", foreign_keys=[parent_id], back_populates="children_links")
    student = relationship("User", foreign_keys=[student_id], back_populates="parent_links")

    __table_args__ = (
        sa.UniqueConstraint("parent_id", "student_id", name="uq_parent_links_pair"),
        sa.CheckConstraint("parent_id != student_id", name="ck_parent_links_distinct"),
    )
