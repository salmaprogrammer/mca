"""Budget module: income overview, expense management, and period reports.

Admin-only throughout (require_role(Role.ADMIN)) — this is centre-owner
financial data, not something staff generally need to see.
"""

from __future__ import annotations

from datetime import date

from flask import abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from app.blueprints.finance import bp
from app.blueprints.finance.forms import FixedExpenseForm, OtherExpenseForm
from app.decorators import require_role
from app.extensions import db
from app.models.enums import ExpenseCategory, Role
from app.models.finance import Expense
from app.services import finance as finance_service


def _selected_month() -> date:
    period = request.args.get("period")
    today = date.today()
    if period:
        try:
            year, month = period.split("-")
            return date(int(year), int(month), 1)
        except (ValueError, TypeError):
            pass
    return date(today.year, today.month, 1)


@bp.route("/")
@require_role(Role.ADMIN)
def overview():
    on = _selected_month()
    locale = "ar"
    report = finance_service.income_for_month(on, locale=locale)
    return render_template("finance/overview.html", on=on, report=report)


@bp.route("/expenses", methods=["GET", "POST"])
@require_role(Role.ADMIN)
def expenses():
    on = _selected_month()

    if request.method == "POST" and request.form.get("action") == "save_fixed":
        category_raw = request.form.get("category")
        amount = request.form.get("amount_egp")
        try:
            category = ExpenseCategory(category_raw)
        except ValueError:
            abort(400)
        if category == ExpenseCategory.OTHER:
            abort(400)
        try:
            finance_service.set_fixed_expense(current_user, category, on, amount or 0)
            flash(_("Expense saved."), "success")
        except (finance_service.FinanceError, TypeError, ValueError):
            flash(_("That is not a valid amount."), "error")
        return redirect(url_for("finance.expenses", period=on.strftime("%Y-%m")))

    other_form = OtherExpenseForm()
    if request.method == "POST" and request.form.get("action") == "add_other":
        if other_form.validate_on_submit():
            finance_service.add_other_expense(
                current_user, on, other_form.amount_egp.data, other_form.note.data
            )
            flash(_("Expense added."), "success")
            return redirect(url_for("finance.expenses", period=on.strftime("%Y-%m")))

    all_expenses = finance_service.expenses_for_month(on)
    fixed_by_category = {
        e.category: e for e in all_expenses if e.category != ExpenseCategory.OTHER
    }
    other_expenses = [e for e in all_expenses if e.category == ExpenseCategory.OTHER]
    total = sum((e.amount_egp for e in all_expenses), start=all_expenses[0].amount_egp * 0) \
        if all_expenses else 0

    return render_template(
        "finance/expenses.html",
        on=on,
        fixed_categories=finance_service.FIXED_CATEGORIES,
        fixed_by_category=fixed_by_category,
        other_expenses=other_expenses,
        other_form=other_form,
        total=total,
    )


@bp.route("/expenses/other/<int:expense_id>/delete", methods=["POST"])
@require_role(Role.ADMIN)
def delete_other_expense(expense_id: int):
    expense = db.session.get(Expense, expense_id)
    if expense is None or expense.category != ExpenseCategory.OTHER:
        abort(404)
    on = expense.month
    finance_service.delete_expense(current_user, expense)
    flash(_("Expense removed."), "success")
    return redirect(url_for("finance.expenses", period=on.strftime("%Y-%m")))


@bp.route("/reports")
@require_role(Role.ADMIN)
def reports():
    on = _selected_month()
    quarter = (on.month - 1) // 3 + 1
    half = 1 if on.month <= 6 else 2

    monthly = finance_service.monthly_report(on)
    quarterly = finance_service.quarterly_report(on.year, quarter)
    half_yearly = finance_service.half_year_report(on.year, half)
    yearly = finance_service.yearly_report(on.year)

    return render_template(
        "finance/reports.html",
        on=on,
        monthly=monthly,
        quarterly=quarterly,
        half_yearly=half_yearly,
        yearly=yearly,
    )
