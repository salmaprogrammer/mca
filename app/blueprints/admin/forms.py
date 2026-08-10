from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Length


class AssistantForm(FlaskForm):
    full_name = StringField(_l("Full name"), validators=[DataRequired(), Length(max=160)])
    phone = StringField(
        _l("Phone number"),
        validators=[DataRequired()],
        render_kw={"placeholder": "01xxxxxxxxx", "dir": "ltr"},
    )
    submit = SubmitField(_l("Create assistant"))
