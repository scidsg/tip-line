import html
import re
from typing import Any, Mapping

from flask_wtf import FlaskForm
from markupsafe import Markup
from wtforms import Field, Form, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, ValidationError
from wtforms.widgets.core import html_params

from hushline.model import MessageStatus
from hushline.safe_template import TemplateError, safe_render_template


class Button:
    def __call__(self, field: Field, **kwargs: Any) -> Markup:
        kwargs.setdefault("id", field.id)
        kwargs.setdefault("type", "submit")
        kwargs.setdefault("value", field.label.text)
        if "value" not in kwargs:
            kwargs["value"] = field._value()
        if "required" not in kwargs and "required" in getattr(field, "flags", []):
            kwargs["required"] = True
        params = html_params(name=field.name, **kwargs)
        return Markup(f"<button {params}>{kwargs['value']}</button>")


class DisplayNoneButton(Button):
    def __call__(self, field: Field, **kwargs: Any) -> Markup:
        kwargs.setdefault("class", "")
        if kwargs["class"]:
            kwargs["class"] += " "
        kwargs["class"] += "display-none"
        return super().__call__(field, **kwargs)


class ComplexPassword:
    def __init__(self, message: str | None = None) -> None:
        if not message:
            message = (
                "⛔️ Password must include uppercase, lowercase, digit, and a special character."
            )
        self.message = message

    # TODO the regexes here should be fields in the class so they are compiled once
    def __call__(self, form: Form, field: Field) -> None:
        password = field.data
        if not (
            re.search("[A-Z]", password)
            and re.search("[a-z]", password)
            and re.search("[0-9]", password)
            and re.search("[^A-Za-z0-9]", password)
        ):
            raise ValidationError(self.message)


class HexColor:
    # HTML input color elements only give & accept 6-hexit color codes
    hex_color_regex: re.Pattern = re.compile(r"^#[0-9a-fA-F]{6}$")

    def __call__(self, form: Form, field: Field) -> None:
        color: str = field.data
        if not self.hex_color_regex.match(color):
            raise ValidationError(f"{color=} is an invalid 6-hexit color code. (eg. #7d25c1)")


class CanonicalHTML:
    def __call__(self, form: Form, field: Field) -> None:
        text: str = field.data
        if text != html.escape(text).strip():
            raise ValidationError(f"{text=} is ambiguous or unescaped.")


class TwoFactorForm(FlaskForm):
    verification_code = StringField("2FA Code", validators=[DataRequired(), Length(min=6, max=6)])


def coerce_status(status: str | MessageStatus) -> MessageStatus:
    if isinstance(status, MessageStatus):
        return status
    return MessageStatus[status.upper()]


class UpdateMessageStatusForm(FlaskForm):
    status = SelectField(
        choices=[(x.name, x.display_str) for x in MessageStatus],
        validators=[DataRequired()],
        coerce=coerce_status,
    )
    submit = SubmitField("Update", widget=Button())


class DeleteMessageForm(FlaskForm):
    submit = SubmitField("Delete", widget=Button())


class ValidTemplate:
    def __init__(self, variables: Mapping[str, str]) -> None:
        self._variables = variables

    def __call__(self, form: Form, field: Field) -> None:
        try:
            safe_render_template(field.data, self._variables)
        except TemplateError as e:
            raise ValidationError(str(e))
