import pytest
from flask import url_for
from flask.testing import FlaskClient

from hushline.db import db
from hushline.model import FieldValue, Message, User, Username


@pytest.mark.usefixtures("_authenticated_user")
@pytest.mark.usefixtures("_pgp_user")
def test_delete_own_message(client: FlaskClient, user: User) -> None:
    # Create a message for the authenticated user
    message = Message(username_id=user.primary_username.id)
    db.session.add(message)
    db.session.commit()

    for field_def in user.primary_username.message_fields:
        field_value = FieldValue(
            field_def,
            message,
            "test_value",
            field_def.encrypted,
        )
        db.session.add(field_value)
        db.session.commit()

    # Attempt to delete the user's own message
    response = client.post(
        url_for("delete_message", id=message.id),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Message deleted successfully" in response.text
    assert db.session.get(Message, message.id) is None  # Ensure message was deleted


@pytest.mark.usefixtures("_authenticated_user")
def test_cannot_delete_other_user_message(
    client: FlaskClient, user: User, user_password: str
) -> None:
    # Create another user within the test
    other_user = User(password=user_password)
    db.session.add(other_user)
    db.session.flush()

    other_username = Username(user_id=other_user.id, _username="otheruser", is_primary=True)
    db.session.add(other_username)
    db.session.commit()

    # Create a message for the other user
    other_user_message = Message(username_id=other_username.id)
    db.session.add(other_user_message)
    db.session.commit()

    for field_def in other_username.message_fields:
        field_value = FieldValue(
            field_def,
            other_user_message,
            "test_value",
            field_def.encrypted,
        )
        db.session.add(field_value)
        db.session.commit()

    # Attempt to delete the other user's message
    response = client.post(
        url_for("delete_message", id=other_user_message.id),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Message not found" in response.text
    assert (
        db.session.get(Message, other_user_message.id) is not None
    )  # Ensure message was not deleted
