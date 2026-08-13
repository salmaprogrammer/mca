from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional

SUBJECTS = [
    "Math",
    "Physics",
    "Chemistry",
    "Biology",
    "English",
    "SAT",
    "GPA Prep",
    "EST Prep",
]

_PHONE_KW = {"placeholder": "01xxxxxxxxx", "dir": "ltr"}


class TeacherForm(FlaskForm):
    full_name = StringField(_l("Teacher name"), validators=[DataRequired(), Length(max=160)])
    phone = StringField(_l("Phone number"), validators=[DataRequired()], render_kw=_PHONE_KW)
    subject = SelectField(
        _l("Subject"),
        choices=[(s, s) for s in SUBJECTS],
        validators=[Optional()],
    )
    submit = SubmitField(_l("Create teacher account"))


class StudentForm(FlaskForm):
    """One phone is enough: see OPEN QUESTION 1 in PLAN.md §8.

    If only one number is supplied it becomes the *parent's* login and the
    student is created without one, rather than the two sharing a username.
    """

    student_name = StringField(_l("Student name"), validators=[DataRequired(), Length(max=160)])
    student_phone = StringField(
        _l("Student phone (optional)"), validators=[Optional()], render_kw=_PHONE_KW
    )
    parent_name = StringField(_l("Parent name"), validators=[Optional(), Length(max=160)])
    parent_phone = StringField(
        _l("Parent phone"), validators=[DataRequired()], render_kw=_PHONE_KW
    )
    school = StringField(_l("School (optional)"), validators=[Optional(), Length(max=120)])
    grade = StringField(_l("Grade (optional)"), validators=[Optional(), Length(max=40)])
    submit = SubmitField(_l("Create student and parent accounts"))


_USERNAME_KW = {"placeholder": "sara.h", "dir": "ltr", "autocomplete": "off"}


class StudentEditForm(FlaskForm):
    full_name = StringField(_l("Student name"), validators=[DataRequired(), Length(max=160)])
    phone = StringField(_l("Phone (leave blank to remove login)"),
                        validators=[Optional()], render_kw=_PHONE_KW)
    username = StringField(_l("Username (optional)"),
                           validators=[Optional(), Length(max=24)],
                           render_kw=_USERNAME_KW)
    school = StringField(_l("School"), validators=[Optional(), Length(max=120)])
    grade = StringField(_l("Grade"), validators=[Optional(), Length(max=40)])
    submit = SubmitField(_l("Save changes"))


class TeacherEditForm(FlaskForm):
    full_name = StringField(_l("Teacher name"), validators=[DataRequired(), Length(max=160)])
    phone = StringField(_l("Phone"), validators=[DataRequired()], render_kw=_PHONE_KW)
    username = StringField(_l("Username (optional)"),
                           validators=[Optional(), Length(max=24)],
                           render_kw=_USERNAME_KW)
    subject = SelectField(
        _l("Subject"),
        choices=[(s, s) for s in SUBJECTS],
        validators=[Optional()],
    )
    submit = SubmitField(_l("Save changes"))


class ParentEditForm(FlaskForm):
    """Parents get a lighter form — no school/grade/subject."""

    full_name = StringField(_l("Parent name"), validators=[DataRequired(), Length(max=160)])
    phone = StringField(_l("Phone"), validators=[DataRequired()], render_kw=_PHONE_KW)
    username = StringField(_l("Username (optional)"),
                           validators=[Optional(), Length(max=24)],
                           render_kw=_USERNAME_KW)
    submit = SubmitField(_l("Save changes"))
