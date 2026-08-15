"""Monthly operating expenses (budget module).

Income needs no separate table: it is derived entirely from existing
`Enrollment` rows (amount_due, payment_status, paid_at). Only expenses get a
table here, since they are not tied to any other record.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin
from app.models.enums import ExpenseCategory, enum_column


class Expense(TimestampMixin, db.Model):
    """One line of monthly spending.

    The six fixed bills (rent, salary, utilities, partner share) get one row
    per month, edited in place — that is what "an editable amount" means for
    a recurring bill. OTHER is different: it can have several rows in the
    same month, each with its own `note` (e.g. "AC maintenance", "new
    chairs"), since ad-hoc spending isn't one number.
    """

    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[ExpenseCategory] = mapped_column(
        enum_column(ExpenseCategory, 24), nullable=False, index=True
    )
    # Always the first day of the month; normalises "which month" to one
    # comparable value regardless of when in the month it was entered.
    month: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    amount_egp: Mapped[Decimal] = mapped_column(sa.Numeric(10, 2), default=0, nullable=False)
    note: Mapped[str | None] = mapped_column(sa.String(255))
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))

    created_by = relationship("User", foreign_keys=[created_by_id])

    def __repr__(self) -> str:
        return f"<Expense {self.id} {self.category.value} {self.month}>"
