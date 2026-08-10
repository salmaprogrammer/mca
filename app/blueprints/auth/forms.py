from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, EqualTo, Length

MIN_PASSWORD_LENGTH = 8


class LoginForm(FlaskForm):
    identifier = StringField(
        _l("Phone number"), validators=[DataRequired()], render_kw={"placeholder": "01xxxxxxxxx"}
    )
    password = PasswordField(_l("Password"), validators=[DataRequired()])
    submit = SubmitField(_l("Sign in"))


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(_l("Current password"), validators=[DataRequired()])
    new_password = PasswordField(
        _l("New password"),
        validators=[
            DataRequired(),
            Length(min=MIN_PASSWORD_LENGTH, message=_l("Use at least 8 characters.")),
        ],
    )
    confirm_password = PasswordField(
        _l("Confirm new password"),
        validators=[DataRequired(), EqualTo("new_password", message=_l("Passwords do not match."))],
    )
    submit = SubmitField(_l("Save password"))


class TermsForm(FlaskForm):
    agree = BooleanField(
        _l("I agree to the terms and conditions"),
        validators=[DataRequired(message=_l("You must agree before continuing."))],
    )
    submit = SubmitField(_l("Continue to dashboard"))
