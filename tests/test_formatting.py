"""Tests for the 12-hour time formatter (app/formatting.py)."""

from __future__ import annotations

from datetime import datetime, time

from flask_babel import force_locale

from app.formatting import format_time_12h


class TestFormatTime12h:
    def test_none_is_a_dash(self, app):
        with app.test_request_context():
            assert format_time_12h(None) == "—"

    def test_english_uses_am_pm(self, app):
        with app.test_request_context(), force_locale("en"):
            assert format_time_12h(time(16, 30)) == "4:30 PM"
            assert format_time_12h(time(9, 5)) == "9:05 AM"
            assert format_time_12h(time(0, 0)) == "12:00 AM"
            assert format_time_12h(time(12, 0)) == "12:00 PM"

    def test_arabic_uses_the_arabic_markers(self, app):
        with app.test_request_context(), force_locale("ar"):
            assert format_time_12h(time(16, 30)) == "4:30 م"
            assert format_time_12h(time(9, 5)) == "9:05 ص"

    def test_it_accepts_a_naive_datetime(self, app):
        with app.test_request_context(), force_locale("en"):
            assert format_time_12h(datetime(2026, 8, 13, 17, 45)) == "5:45 PM"

    def test_the_jinja_filter_is_registered(self, app):
        assert "time12" in app.jinja_env.filters
        with app.test_request_context(), force_locale("en"):
            rendered = app.jinja_env.from_string(
                "{{ t|time12 }}"
            ).render(t=time(16, 30))
            assert rendered == "4:30 PM"
