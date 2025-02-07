from flask_wtf import FlaskForm
from wtforms import (
    PasswordField,
    RadioField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, Optional
from wtforms.widgets import CheckboxInput, ListWidget

from hushline.forms import ComplexPassword
from hushline.model import FieldDefinition, FieldType
from hushline.routes.common import valid_username


# https://wtforms.readthedocs.io/en/3.2.x/specific_problems/#specialty-field-tricks
class MultiCheckboxField(SelectMultipleField):
    """
    A multiple-select, except displays a list of checkboxes.

    Iterating the field will produce subfields, allowing custom rendering of
    the enclosed checkbox fields.
    """

    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


class TwoFactorForm(FlaskForm):
    verification_code = StringField("2FA Code", validators=[DataRequired(), Length(min=6, max=6)])


class RegistrationForm(FlaskForm):
    username = StringField(
        "Username", validators=[DataRequired(), Length(min=4, max=25), valid_username]
    )
    password = PasswordField(
        "Password",
        validators=[
            DataRequired(),
            Length(min=18, max=128),
            ComplexPassword(),
        ],
    )
    invite_code = StringField("Invite Code", validators=[DataRequired(), Length(min=6, max=25)])


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])


class DynamicMessageForm:
    def __init__(self, fields: list[FieldDefinition]):
        self.fields = fields

        # Create a custom form class for this instance of CustomMessageForm
        class F(FlaskForm):
            pass

        self.F = F

        # Custom validator to skip choice validation while keeping other validations
        def skip_invalid_choice(
            form: FlaskForm, field: RadioField | SelectField | MultiCheckboxField
        ) -> None:
            field.errors = [error for error in field.errors if error != "Not a valid choice."]

        # Add the fields to the form
        for i, field in enumerate(fields):
            # Skip disabled fields
            if not field.enabled:
                continue

            # Define the validators
            validators = []

            # Required or optional
            if field.required:
                validators.append(DataRequired())
            else:
                validators.append(Optional())

            # Multiline text has 102400 chars, all other types (types, single choice, and multiple
            # choice) have 10240 chars. We're using huge values because we're storing PGP-encrypted
            # data.
            if field.field_type == FieldType.MULTILINE_TEXT:
                validators.append(Length(max=102400))
            else:
                validators.append(Length(max=10240))

            # If it's an encrypted choice field, skip validating the PGP-encrypted choices
            if field.encrypted and field.field_type in (
                FieldType.CHOICE_SINGLE,
                FieldType.CHOICE_MULTIPLE,
            ):
                validators.append(skip_invalid_choice)

            # Add the field to the form
            name = f"field_{i}"
            if field.field_type == FieldType.TEXT:
                setattr(
                    self.F,
                    name,
                    StringField(
                        field.label, validators=validators, render_kw={"autocomplete": "off"}
                    ),
                )
            elif field.field_type == FieldType.MULTILINE_TEXT:
                setattr(self.F, name, TextAreaField(field.label, validators=validators))
            elif field.field_type == FieldType.CHOICE_SINGLE:
                # Decide if we want radio buttons or dropdown depending on the number of choices
                field_type = RadioField if len(field.choices) <= 3 else SelectField  # noqa: PLR2004
                setattr(
                    self.F,
                    name,
                    field_type(
                        field.label, choices=field.choices, validators=validators, coerce=str
                    ),
                )
            elif field.field_type == FieldType.CHOICE_MULTIPLE:
                setattr(
                    self.F,
                    name,
                    MultiCheckboxField(
                        field.label, choices=field.choices, validators=validators, coerce=str
                    ),
                )
            else:
                raise ValueError(f"Unknown field type: {field.field_type}")

    def field_data(self) -> list[dict[str, str | FieldDefinition]]:
        """
        Return a list of dicts for this form for the template to loop through while rendering
        """
        return [
            {
                "name": f"field_{i}",
                "field": self.fields[i],
            }
            for i in range(len(self.fields))
        ]

    def field_from_name(self, name: str) -> FieldDefinition | None:
        """
        Return the FieldDefinition object for the given field name
        """
        for i, field in enumerate(self.fields):
            if f"field_{i}" == name:
                return field
        return None

    def form(self) -> FlaskForm:
        """
        Return an instance of the custom form class
        """
        return self.F()
