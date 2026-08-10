"""Course form (sprints S2.2, S2.3).

Slot rows are parsed from the raw request rather than a WTForms `FieldList`:
their number is decided by the selected course type and re-rendered over HTMX,
and a fixed-length FieldList fights that.
"""

from __future__ import annotations

from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import (
    BooleanField,
    DateField,
    DecimalField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.models.course import WEEK_ORDER, weekday_label


class CourseForm(FlaskForm):
    name = StringField(
        _l("Course name"),
        validators=[DataRequired(), Length(max=160)],
        description=_l("Internal label, e.g. “November round — Mr Ahmed”"),
    )
    course_type_id = SelectField(_l("Course type"), coerce=int, validators=[DataRequired()])
    teacher_id = SelectField(_l("Teacher"), coerce=int, validators=[DataRequired()])
    description = TextAreaField(_l("Description"), validators=[Optional()])
    price_egp = DecimalField(
        _l("Price (EGP)"), validators=[Optional(), NumberRange(min=0)], places=2, default=0
    )
    start_date = DateField(_l("Start date"), validators=[Optional()])
    trial_enabled = BooleanField(_l("Allow trial session booking"))
    cover_image = FileField(
        _l("Cover image"),
        validators=[
            Optional(),
            FileAllowed(["jpg", "jpeg", "png", "webp"], _l("Images only (jpg, png, webp).")),
        ],
    )
    submit = SubmitField(_l("Save course"))

    def load_choices(self, course_types, teachers) -> None:
        self.course_type_id.choices = [
            (t.id, t.label_en) for t in course_types
        ]
        self.teacher_id.choices = [(0, str(_l("Select teacher")))] + [
            (
                t.id,
                f"{t.full_name} — {t.teacher_profile.subject}"
                if t.teacher_profile and t.teacher_profile.subject
                else t.full_name,
            )
            for t in teachers
        ]


def weekday_choices(locale: str = "en") -> list[tuple[int, str]]:
    """Days in the order an Egyptian week is read, Saturday first."""
    return [(day, weekday_label(day, locale)) for day in WEEK_ORDER]


def parse_slots(form_data, expected: int) -> list[dict]:
    """Pull `slot_weekday_N` / `slot_time_N` pairs out of the submitted form."""
    slots = []
    for index in range(expected):
        weekday = form_data.get(f"slot_weekday_{index}")
        start = form_data.get(f"slot_time_{index}")
        if weekday in (None, "") or start in (None, ""):
            continue
        slots.append({"weekday": int(weekday), "start_time": start})
    return slots
