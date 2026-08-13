"""Locale-aware display helpers.

Times are shown 12-hour ("4:30 PM" in English, "4:30 م" in Arabic) so parents
reading the WhatsApp update or a session card see the format they read a clock
in. Digits stay Western in both locales — every other number in the UI (prices,
phone numbers, sequence counts) is Western, and mixing them here would look
wrong on a bilingual page.
"""

from __future__ import annotations

from datetime import datetime, time

from flask_babel import get_locale


def format_time_12h(value: time | datetime | None) -> str:
    """Render a `time` or `datetime` as 12-hour with AM/PM in the active locale.

    Returns "—" for None so template calls do not need a `{% if %}` guard.
    """
    if value is None:
        return "—"
    if isinstance(value, datetime):
        value = value.timetz() if value.tzinfo else value.time()

    hour = value.hour % 12 or 12
    is_arabic = str(get_locale() or "").startswith("ar")
    marker = ("م" if value.hour >= 12 else "ص") if is_arabic else (
        "PM" if value.hour >= 12 else "AM"
    )
    return f"{hour}:{value.minute:02d} {marker}"
