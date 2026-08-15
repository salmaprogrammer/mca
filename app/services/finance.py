"""Budget module: income (derived from enrollments) and expenses.

Income is never stored separately — it is always computed fresh from
`Enrollment` rows, so it can never drift out of sync with the payments an
assistant actually records elsewhere in the app. Expenses are the only thing
this module persists.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import select

from app.extensions import db
from app.models.course import Course, Enrollment
from app.models.enums import ExpenseCategory, PaymentStatus
from app.models.finance import Expense
from app.models.user import User
from app.services import audit

FIXED_CATEGORIES = [
    ExpenseCategory.RENT,
    ExpenseCategory.ASSISTANT_SALARY,
    ExpenseCategory.ELECTRICITY,
    ExpenseCategory.INTERNET,
    ExpenseCategory.WATER,
    ExpenseCategory.GAS,
    ExpenseCategory.PARTNER_SHARE,
]


class FinanceError(ValueError):
    """Something the operator needs explained."""


def month_start(on: date) -> date:
    return on.replace(day=1)


def month_end(on: date) -> date:
    _, last_day = monthrange(on.year, on.month)
    return on.replace(day=last_day)


# ------------------------------------------------------------------ income


@dataclass
class IncomeLine:
    student_name: str
    course_title: str
    amount: Decimal


@dataclass
class IncomeReport:
    lines: list[IncomeLine] = field(default_factory=list)
    total: Decimal = Decimal("0")
    outstanding_lines: list[IncomeLine] = field(default_factory=list)
    outstanding_total: Decimal = Decimal("0")


def income_for_range(start: date, end: date, locale: str = "en") -> IncomeReport:
    """Every enrolment paid within [start, end], plus everything still owed
    right now regardless of range — a family that owes money for June is
    still owed today, not just "in June"."""
    from app.models.base import UTC

    start_dt = datetime.combine(start, time.min, tzinfo=UTC)
    end_dt = datetime.combine(end, time.min, tzinfo=UTC)

    paid_rows = db.session.scalars(
        select(Enrollment)
        .join(Course)
        .where(
            Enrollment.payment_status == PaymentStatus.PAID,
            Enrollment.paid_at >= start_dt,
            Enrollment.paid_at < end_dt,
        )
    ).all()

    unpaid_rows = db.session.scalars(
        select(Enrollment).where(Enrollment.payment_status == PaymentStatus.UNPAID)
    ).all()

    report = IncomeReport()
    for e in paid_rows:
        line = IncomeLine(
            student_name=e.student.full_name,
            course_title=e.course.display_title(locale),
            amount=e.amount_due,
        )
        report.lines.append(line)
        report.total += e.amount_due

    for e in unpaid_rows:
        line = IncomeLine(
            student_name=e.student.full_name,
            course_title=e.course.display_title(locale),
            amount=e.amount_due,
        )
        report.outstanding_lines.append(line)
        report.outstanding_total += e.amount_due

    return report


def income_for_month(on: date, locale: str = "en") -> IncomeReport:
    start = month_start(on)
    end = month_start(_add_months(on, 1))
    return income_for_range(start, end, locale)


def _add_months(on: date, count: int) -> date:
    month_index = on.month - 1 + count
    year = on.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


# --------------------------------------------------------------- expenses


def expenses_for_month(on: date) -> list[Expense]:
    start = month_start(on)
    return list(
        db.session.scalars(
            select(Expense).where(Expense.month == start).order_by(Expense.category, Expense.id)
        )
    )


def set_fixed_expense(
    actor: User, category: ExpenseCategory, on: date, amount: Decimal | float | str
) -> Expense:
    """Create-or-update the single row for a fixed monthly bill."""
    if category == ExpenseCategory.OTHER:
        raise FinanceError("Use add_other_expense for the OTHER category.")

    start = month_start(on)
    existing = db.session.scalar(
        select(Expense).where(Expense.category == category, Expense.month == start)
    )
    amount_dec = Decimal(str(amount or 0))

    if existing:
        before = audit.snapshot(existing, ["amount_egp"])
        existing.amount_egp = amount_dec
        audit.record(
            "expense.update",
            "expense",
            entity_id=existing.id,
            actor=actor,
            before=before,
            after=audit.snapshot(existing, ["amount_egp"]),
        )
        db.session.commit()
        return existing

    expense = Expense(
        category=category, month=start, amount_egp=amount_dec, created_by_id=actor.id
    )
    db.session.add(expense)
    db.session.flush()
    audit.record(
        "expense.create", "expense", entity_id=expense.id, actor=actor,
        after=audit.snapshot(expense),
    )
    db.session.commit()
    return expense


def add_other_expense(
    actor: User, on: date, amount: Decimal | float | str, note: str
) -> Expense:
    expense = Expense(
        category=ExpenseCategory.OTHER,
        month=month_start(on),
        amount_egp=Decimal(str(amount or 0)),
        note=(note or "").strip() or None,
        created_by_id=actor.id,
    )
    db.session.add(expense)
    db.session.flush()
    audit.record(
        "expense.create", "expense", entity_id=expense.id, actor=actor,
        after=audit.snapshot(expense),
    )
    db.session.commit()
    return expense


def delete_expense(actor: User, expense: Expense) -> None:
    before = audit.snapshot(expense)
    audit.record(
        "expense.delete", "expense", entity_id=expense.id, actor=actor, before=before,
    )
    db.session.delete(expense)
    db.session.commit()


def total_expenses_for_range(start: date, end: date) -> Decimal:
    total = db.session.scalar(
        select(sa.func.coalesce(sa.func.sum(Expense.amount_egp), 0)).where(
            Expense.month >= start, Expense.month < end
        )
    )
    return Decimal(str(total or 0))


# ---------------------------------------------------------------- reports


@dataclass
class PeriodReport:
    label: str
    start: date
    end: date
    income_total: Decimal
    expense_total: Decimal

    @property
    def net(self) -> Decimal:
        return self.income_total - self.expense_total


def _period_report(label: str, start: date, end: date) -> PeriodReport:
    income = income_for_range(start, end).total
    expenses = total_expenses_for_range(start, end)
    return PeriodReport(label=label, start=start, end=end, income_total=income, expense_total=expenses)


def monthly_report(on: date) -> PeriodReport:
    start = month_start(on)
    end = month_start(_add_months(on, 1))
    return _period_report(start.strftime("%Y-%m"), start, end)


def quarterly_report(year: int, quarter: int) -> PeriodReport:
    start_month = (quarter - 1) * 3 + 1
    start = date(year, start_month, 1)
    end = month_start(_add_months(start, 3))
    return _period_report(f"{year} Q{quarter}", start, end)


def half_year_report(year: int, half: int) -> PeriodReport:
    start_month = 1 if half == 1 else 7
    start = date(year, start_month, 1)
    end = month_start(_add_months(start, 6))
    return _period_report(f"{year} H{half}", start, end)


def yearly_report(year: int) -> PeriodReport:
    start = date(year, 1, 1)
    end = date(year + 1, 1, 1)
    return _period_report(str(year), start, end)
