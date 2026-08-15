from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional

TITLE_CHOICES = [
    ("", _l("Assistant (default)")),
    ("General Manager (GM)", "General Manager (GM)"),
    ("Academic Manager", "Academic Manager"),
    ("Academic Principal", "Academic Principal"),
    ("other", _l("Other…")),
]


class AssistantForm(FlaskForm):
    full_name = StringField(_l("Full name"), validators=[DataRequired(), Length(max=160)])
    phone = StringField(
        _l("Phone number"),
        validators=[DataRequired()],
        render_kw={"placeholder": "01xxxxxxxxx", "dir": "ltr"},
    )
    title_choice = SelectField(_l("Job title"), choices=TITLE_CHOICES, validators=[Optional()])
    title_other = StringField(
        _l("Custom title"), validators=[Optional(), Length(max=80)]
    )
    submit = SubmitField(_l("Create assistant"))

    @property
    def resolved_title(self) -> str | None:
        """The title actually being set: the custom text if 'Other' was
        picked, the preset value otherwise, or None for the default."""
        if self.title_choice.data == "other":
            return (self.title_other.data or "").strip() or None
        return self.title_choice.data or None
