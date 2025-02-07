from uuid import uuid4

import pytest
from bs4 import BeautifulSoup
from flask import Flask, url_for
from flask.testing import FlaskClient

from hushline.db import db
from hushline.model import Message, OrganizationSetting, User, Username

msg_contact_method = "I prefer Signal."
msg_content = "This is a test message."

pgp_message_sig = "-----BEGIN PGP MESSAGE-----\n\n"


def get_captcha_from_session(client: FlaskClient, username: str) -> str:
    # Simulate loading the profile page to generate and retrieve the CAPTCHA from the session
    response = client.get(url_for("profile", username=username))
    assert response.status_code == 200

    with client.session_transaction() as session:
        captcha_answer = session.get("math_answer")
        assert captcha_answer
        return captcha_answer


@pytest.mark.usefixtures("_pgp_user")
def test_profile_header(client: FlaskClient, user: User) -> None:
    assert (
        db.session.scalars(
            db.select(OrganizationSetting).filter_by(
                key=OrganizationSetting.BRAND_PROFILE_HEADER_TEMPLATE
            )
        ).one_or_none()
        is None
    )  # precondition

    resp = client.get(url_for("profile", username=user.primary_username.username))
    assert resp.status_code == 200
    assert (
        "Submit message to "
        + (user.primary_username.display_name or user.primary_username.username)
        in resp.text
    )

    rand = str(uuid4())
    template = rand + " {{ display_name_or_username }} {{ username }} {{ display_name }}"
    OrganizationSetting.upsert(OrganizationSetting.BRAND_PROFILE_HEADER_TEMPLATE, template)
    db.session.commit()

    expected = (
        f"{rand} {user.primary_username.display_name or user.primary_username.username} "
        f"{user.primary_username.username} {user.primary_username.display_name or ''}"
    )
    resp = client.get(url_for("profile", username=user.primary_username.username))
    assert resp.status_code == 200
    assert expected in resp.text


@pytest.mark.usefixtures("_authenticated_user")
@pytest.mark.usefixtures("_pgp_user")
def test_profile_submit_message(client: FlaskClient, user: User) -> None:
    response = client.post(
        url_for("profile", username=user.primary_username.username),
        data={
            "field_0": msg_contact_method,
            "field_1": msg_content,
            "captcha_answer": get_captcha_from_session(client, user.primary_username.username),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200, response.text
    assert "Message submitted successfully." in response.text

    message = db.session.scalars(
        db.select(Message).filter_by(username_id=user.primary_username.id)
    ).one()
    assert len(message.field_values) == 2
    for field_value in message.field_values:
        assert pgp_message_sig in field_value.value

    response = client.get(url_for("message", id=message.id), follow_redirects=True)
    assert response.status_code == 200
    assert pgp_message_sig in response.text, response.text


@pytest.mark.usefixtures("_authenticated_user")
@pytest.mark.usefixtures("_pgp_user")
def test_profile_submit_message_to_alias(
    client: FlaskClient, user: User, user_alias: Username
) -> None:
    response = client.post(
        url_for("profile", username=user_alias.username),
        data={
            "field_0": msg_contact_method,
            "field_1": msg_content,
            "captcha_answer": get_captcha_from_session(client, user.primary_username.username),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200, response.text
    assert "Message submitted successfully." in response.text

    message = db.session.scalars(db.select(Message).filter_by(username_id=user_alias.id)).one()
    assert len(message.field_values) == 2
    for field_value in message.field_values:
        assert pgp_message_sig in field_value.value

    response = client.get(url_for("message", id=message.id), follow_redirects=True)
    assert response.status_code == 200
    assert pgp_message_sig in response.text, response.text


@pytest.mark.usefixtures("_authenticated_user")
def test_profile_pgp_required(client: FlaskClient, app: Flask, user: User) -> None:
    response = client.get(url_for("profile", username=user.primary_username.username))
    assert response.status_code == 200

    assert 'id="messageForm"' in response.text
    assert "You can't send encrypted messages to this user through Hush Line" not in response.text

    user.pgp_key = None
    db.session.commit()

    response = client.get(url_for("profile", username=user.primary_username.username))
    assert response.status_code == 200
    assert "Sending messages is disabled" in response.text


@pytest.mark.usefixtures("_authenticated_user")
def test_profile_extra_fields(client: FlaskClient, app: Flask, user: User) -> None:
    user.primary_username.extra_field_label1 = "Signal username"
    user.primary_username.extra_field_value1 = "singleusername.666"
    user.primary_username.extra_field_label2 = "Arbitrary Link"
    user.primary_username.extra_field_value2 = "https://scidsg.org/"
    user.primary_username.extra_field_label3 = "xss should fail"
    user.primary_username.extra_field_value3 = "<script>alert('xss')</script>"
    db.session.commit()

    response = client.get(url_for("profile", username=user.primary_username.username))
    assert response.status_code == 200

    soup = BeautifulSoup(response.data, "html.parser")
    signal_username_span = soup.find("span", class_="extra-field-value")
    assert signal_username_span is not None
    assert signal_username_span.text.strip() == "singleusername.666"

    link = soup.find("a", href="https://scidsg.org/")
    assert link is not None
    assert link.get("target") == "_blank"
    assert "noopener" in link.get("rel", [])
    assert "noreferrer" in link.get("rel", [])

    # Verify that XSS is correctly escaped
    # Search for the XSS string directly in the HTML with both possible escapes
    html_str = str(soup)
    assert (
        "&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;" in html_str
        or "&lt;script&gt;alert('xss')&lt;/script&gt;" in html_str
    )
    assert "<script>alert('xss')</script>" not in html_str
