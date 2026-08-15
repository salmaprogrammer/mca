from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import DecimalField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange, Optional


class FixedExpenseForm(FlaskForm):
    amount_egp = DecimalField(
        _l("Amount (EGP)"), validators=[DataRequired(), NumberRange(min=0)]
    )
    submit = SubmitField(_l("Save"))


class OtherExpenseForm(FlaskForm):
    amount_egp = DecimalField(
        _l("Amount (EGP)"), validators=[DataRequired(), NumberRange(min=0)]
    )
    note = StringField(_l("What for"), validators=[Optional(), Length(max=255)])
    submit = SubmitField(_l("Add expense"))
