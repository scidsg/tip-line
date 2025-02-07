from typing import List

import pytest
from flask.testing import FlaskClient

from hushline.db import db
from hushline.model import FieldDefinition, FieldType, FieldValue, Message, User, Username


@pytest.fixture()
def username(client: FlaskClient) -> Username:
    user = User(password="Test-testtesttesttest-1")  # noqa: S106
    db.session.add(user)
    db.session.commit()

    username = Username(user_id=user.id, _username="testuser", is_primary=True)
    db.session.add(username)
    db.session.commit()

    return username


def test_add_fields_and_move_up(username: Username) -> None:
    # Add 5 fields
    fields: List[FieldDefinition] = []
    for label in ["A", "B", "C", "D", "E"]:
        field = FieldDefinition(
            username=username,
            label=label,
            field_type=FieldType.TEXT,
            required=False,
            enabled=True,
            encrypted=False,
            choices=[],
        )
        db.session.add(field)
        fields.append(field)
    db.session.commit()

    # Verify the order
    ordered_fields = db.session.scalars(
        db.select(FieldDefinition).order_by(FieldDefinition.sort_order)
    ).all()
    assert [field.label for field in ordered_fields] == ["A", "B", "C", "D", "E"]

    # Move field "D" up
    ordered_fields[3].move_up()

    # Verify the order
    ordered_fields = db.session.scalars(
        db.select(FieldDefinition).order_by(FieldDefinition.sort_order)
    ).all()
    assert [field.label for field in ordered_fields] == ["A", "B", "D", "C", "E"]


def test_add_fields_and_move_down(username: Username) -> None:
    # Add 5 fields
    fields: List[FieldDefinition] = []
    for label in ["A", "B", "C", "D", "E"]:
        field = FieldDefinition(
            username=username,
            label=label,
            field_type=FieldType.TEXT,
            required=False,
            enabled=True,
            encrypted=False,
            choices=[],
        )
        db.session.add(field)
        fields.append(field)
    db.session.commit()

    # Verify the order
    ordered_fields = db.session.scalars(
        db.select(FieldDefinition).order_by(FieldDefinition.sort_order)
    ).all()
    assert [field.label for field in ordered_fields] == ["A", "B", "C", "D", "E"]

    # Move field "B" down
    ordered_fields[1].move_down()

    # Verify the order
    ordered_fields = db.session.scalars(
        db.select(FieldDefinition).order_by(FieldDefinition.sort_order)
    ).all()
    assert [field.label for field in ordered_fields] == ["A", "C", "B", "D", "E"]


@pytest.mark.usefixtures("_pgp_user")
def test_field_value_encryption(user: User) -> None:
    username = user.primary_username

    field_definition = FieldDefinition(
        username=username,
        label="Test Field",
        field_type=FieldType.TEXT,
        required=False,
        enabled=True,
        encrypted=True,  # encrypted field
        choices=[],
    )
    db.session.add(field_definition)
    db.session.commit()

    assert field_definition.encrypted is True

    message = Message(username_id=username.id)
    db.session.add(message)
    db.session.commit()

    field_value = FieldValue(
        field_definition=field_definition,
        message=message,
        value="this is a test value",
        encrypted=field_definition.encrypted,
    )
    db.session.add(field_value)
    db.session.commit()

    val = field_value.value
    assert val is not None
    assert val.startswith("-----BEGIN PGP MESSAGE-----")


@pytest.mark.usefixtures("_pgp_user")
def test_field_value_unencryption(user: User) -> None:
    username = user.primary_username

    field_definition = FieldDefinition(
        username=username,
        label="Test Field",
        field_type=FieldType.TEXT,
        required=False,
        enabled=True,
        encrypted=False,  # not encrypted field
        choices=[],
    )
    db.session.add(field_definition)
    db.session.commit()

    assert field_definition.encrypted is False

    message = Message(username_id=username.id)
    db.session.add(message)
    db.session.commit()

    field_value = FieldValue(
        field_definition=field_definition,
        message=message,
        value="this is a test value",
        encrypted=field_definition.encrypted,
    )
    db.session.add(field_value)
    db.session.commit()

    assert field_value.value == "this is a test value"
