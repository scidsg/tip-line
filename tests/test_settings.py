from base64 import b64decode
from io import BytesIO
from unittest.mock import ANY, MagicMock, patch
from uuid import uuid4

import pytest
from bs4 import BeautifulSoup
from flask import Flask, url_for
from flask.testing import FlaskClient

from hushline.config import AliasMode
from hushline.db import db
from hushline.model import (
    AuthenticationLog,
    Message,
    OrganizationSetting,
    SMTPEncryption,
    User,
    Username,
)
from hushline.settings import (
    ChangePasswordForm,
    ChangeUsernameForm,
    DeleteBrandLogoForm,
    DisplayNameForm,
    EmailForwardingForm,
    NewAliasForm,
    PGPKeyForm,
    SetHomepageUsernameForm,
    UpdateBrandAppNameForm,
    UpdateBrandLogoForm,
    UpdateBrandPrimaryColorForm,
    UpdateDirectoryTextForm,
    UpdateProfileHeaderForm,
    UserGuidanceAddPromptForm,
    UserGuidanceEmergencyExitForm,
    UserGuidanceForm,
    UserGuidancePromptContentForm,
)
from tests.helpers import form_to_data


@pytest.mark.usefixtures("_authenticated_user")
def test_settings_page_loads(client: FlaskClient, user: User) -> None:
    response = client.get(url_for("settings.profile"), follow_redirects=True)
    assert response.status_code == 200
    assert "Settings" in response.text


@pytest.mark.usefixtures("_authenticated_user")
def test_change_display_name(client: FlaskClient, user: User) -> None:
    new_display_name = (user.primary_username.display_name or "") + "_NEW"

    response = client.post(
        url_for("settings.profile"),
        data=form_to_data(
            DisplayNameForm(
                data={
                    "display_name": new_display_name,
                }
            )
        ),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Display name updated successfully" in response.text

    updated_user = db.session.scalars(
        db.select(Username).filter_by(_username=user.primary_username.username)
    ).one()
    assert updated_user.display_name == new_display_name


@pytest.mark.usefixtures("_authenticated_user")
def test_change_username(client: FlaskClient, user: User) -> None:
    new_username = user.primary_username.username + "-new"

    response = client.post(
        url_for("settings.auth"),
        data=form_to_data(
            ChangeUsernameForm(
                data={
                    "new_username": new_username,
                }
            )
        ),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Username changed successfully" in response.text

    updated_user = db.session.scalars(db.select(Username).filter_by(_username=new_username)).one()
    assert updated_user.username == new_username
    assert not updated_user.is_verified


@pytest.mark.usefixtures("_authenticated_user")
def test_change_password(app: Flask, client: FlaskClient, user: User, user_password: str) -> None:
    assert len(original_password_hash := user.password_hash) > 32
    assert original_password_hash.startswith("$scrypt$")
    assert user_password not in original_password_hash
    original_password = user_password

    url = url_for("settings.auth")
    for new_password in [user_password, "", "aB!!", "aB3!", (33 * "aB3!")[:129], 5 * "aB3!"]:
        print(f"Testing: {new_password}")

        # have to use a test request context to "reset" the form fields
        with app.test_request_context():
            form = ChangePasswordForm(
                data={
                    "old_password": user_password,
                    "new_password": new_password,
                }
            )

        response = client.post(url, data=form_to_data(form), follow_redirects=True)
        if (
            user_password != new_password
            and 17 < len(user_password) < 129
            and 17 < len(new_password) < 129
        ):
            assert response.status_code == 200, response.text
            assert "Password successfully changed. Please log in again." in response.text
            assert len(new_password_hash := user.password_hash) > 32
            assert new_password_hash.startswith("$scrypt$")
            assert original_password_hash not in new_password_hash
            assert user_password not in new_password_hash
            assert new_password not in new_password_hash
            user_password = new_password
        elif user_password == new_password:
            assert "Cannot choose a repeat password." in response.text
            assert original_password_hash == user.password_hash
        else:
            assert "Your submitted form could not be processed" in response.text
            assert original_password_hash == user.password_hash

    assert original_password_hash != user.password_hash

    # TODO simulate a log out?

    # Attempt to log in with the registered user's old password
    response = client.post(
        url_for("login"),
        data={
            "username": user.primary_username.username,
            "password": original_password,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Invalid username or password" in response.text
    assert "/login" in response.request.url

    # TODO simulate a log out?

    # Attempt to log in with the registered user's new password
    response = client.post(
        url_for("login"),
        data={
            "username": user.primary_username.username,
            "password": new_password,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Empty Inbox" in response.text
    assert "Invalid username or password" not in response.text
    assert "/inbox" in response.request.url


@pytest.mark.usefixtures("_authenticated_user")
def test_add_pgp_key(client: FlaskClient, user: User, user_password: str) -> None:
    with open("tests/test_pgp_key.txt") as file:
        new_pgp_key = file.read().strip()

    response = client.post(
        url_for("settings.encryption"),
        data=form_to_data(PGPKeyForm(data={"pgp_key": new_pgp_key})),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "PGP key updated successfully" in response.text

    updated_user = db.session.scalars(
        db.select(Username).filter_by(_username=user.primary_username.username)
    ).one()
    assert updated_user.user.pgp_key == new_pgp_key


@pytest.mark.usefixtures("_authenticated_user")
def test_add_invalid_pgp_key(client: FlaskClient, user: User) -> None:
    invalid_pgp_key = "NOT A VALID PGP KEY BLOCK"

    response = client.post(
        url_for("settings.encryption"),
        data=form_to_data(PGPKeyForm(data={"pgp_key": invalid_pgp_key})),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Invalid PGP key format" in response.text

    updated_user = db.session.scalars(
        db.select(Username).filter_by(_username=user.primary_username.username)
    ).one()
    assert updated_user.user.pgp_key != invalid_pgp_key


@pytest.mark.usefixtures("_authenticated_user")
@patch("hushline.email.smtplib.SMTP")
def test_update_smtp_settings_no_pgp(SMTP: MagicMock, client: FlaskClient, user: User) -> None:
    user.pgp_key = None
    db.session.commit()

    response = client.post(
        url_for("settings.notifications"),
        # for some reason using the Form class doesn't work here. why? fuck if i know.
        data={
            "email_address": "primary@example.com",
            "custom_smtp_settings": True,
            "smtp_settings-smtp_server": "smtp.example.com",
            "smtp_settings-smtp_port": 587,
            "smtp_settings-smtp_username": "user@example.com",
            "smtp_settings-smtp_password": "securepassword123",
            "smtp_settings-smtp_encryption": "StartTLS",
            "smtp_settings-smtp_sender": "sender@example.com",
            EmailForwardingForm.submit.name: "",
        },
        follow_redirects=True,
    )
    assert response.status_code == 400
    assert "Email forwarding requires a configured PGP key" in response.text, response.text

    updated_user = (
        db.session.scalars(db.select(Username).filter_by(_username=user.primary_username.username))
        .one()
        .user
    )
    assert updated_user.email is None
    assert updated_user.smtp_server is None
    assert updated_user.smtp_port is None
    assert updated_user.smtp_username is None
    assert updated_user.smtp_password is None


@pytest.mark.usefixtures("_authenticated_user")
@pytest.mark.usefixtures("_pgp_user")
@patch("hushline.email.smtplib.SMTP")
def test_update_smtp_settings_starttls(SMTP: MagicMock, client: FlaskClient, user: User) -> None:
    new_smtp_settings = {
        "email_address": "primary@example.com",
        "custom_smtp_settings": True,
        "smtp_settings-smtp_server": "smtp.example.com",
        "smtp_settings-smtp_port": 587,
        "smtp_settings-smtp_username": "user@example.com",
        "smtp_settings-smtp_password": "securepassword123",
        "smtp_settings-smtp_encryption": "StartTLS",
        "smtp_settings-smtp_sender": "sender@example.com",
        EmailForwardingForm.submit.name: "",
    }

    response = client.post(
        url_for("settings.notifications"),
        data=new_smtp_settings,
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "SMTP settings updated successfully" in response.text

    SMTP.assert_called_with(user.smtp_server, user.smtp_port, timeout=ANY)
    SMTP.return_value.__enter__.return_value.starttls.assert_called_once_with()
    SMTP.return_value.__enter__.return_value.login.assert_called_once_with(
        user.smtp_username, user.smtp_password
    )

    updated_user = (
        db.session.scalars(db.select(Username).filter_by(_username=user.primary_username.username))
        .one()
        .user
    )
    assert updated_user.email == new_smtp_settings["email_address"]
    assert updated_user.smtp_server == new_smtp_settings["smtp_settings-smtp_server"]
    assert updated_user.smtp_port == new_smtp_settings["smtp_settings-smtp_port"]
    assert updated_user.smtp_username == new_smtp_settings["smtp_settings-smtp_username"]
    assert updated_user.smtp_password == new_smtp_settings["smtp_settings-smtp_password"]
    assert updated_user.smtp_encryption.value == new_smtp_settings["smtp_settings-smtp_encryption"]
    assert updated_user.smtp_sender == new_smtp_settings["smtp_settings-smtp_sender"]


@pytest.mark.usefixtures("_authenticated_user")
@pytest.mark.usefixtures("_pgp_user")
@patch("hushline.email.smtplib.SMTP_SSL")
def test_update_smtp_settings_ssl(SMTP: MagicMock, client: FlaskClient, user: User) -> None:
    new_smtp_settings = {
        "email_address": "primary@example.com",
        "custom_smtp_settings": True,
        "smtp_settings-smtp_server": "smtp.example.com",
        "smtp_settings-smtp_port": 465,
        "smtp_settings-smtp_username": "user@example.com",
        "smtp_settings-smtp_password": "securepassword123",
        "smtp_settings-smtp_encryption": "SSL",
        "smtp_settings-smtp_sender": "sender@example.com",
        EmailForwardingForm.submit.name: "",
    }

    response = client.post(
        url_for("settings.notifications"),
        data=new_smtp_settings,
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "SMTP settings updated successfully" in response.text

    SMTP.assert_called_with(user.smtp_server, user.smtp_port, timeout=ANY)
    SMTP.return_value.__enter__.return_value.starttls.assert_not_called()
    SMTP.return_value.__enter__.return_value.login.assert_called_once_with(
        user.smtp_username, user.smtp_password
    )

    updated_user = (
        db.session.scalars(db.select(Username).filter_by(_username=user.primary_username.username))
        .one()
        .user
    )
    assert updated_user.email == new_smtp_settings["email_address"]
    assert updated_user.smtp_server == new_smtp_settings["smtp_settings-smtp_server"]
    assert updated_user.smtp_port == new_smtp_settings["smtp_settings-smtp_port"]
    assert updated_user.smtp_username == new_smtp_settings["smtp_settings-smtp_username"]
    assert updated_user.smtp_password == new_smtp_settings["smtp_settings-smtp_password"]
    assert updated_user.smtp_encryption.value == new_smtp_settings["smtp_settings-smtp_encryption"]
    assert updated_user.smtp_sender == new_smtp_settings["smtp_settings-smtp_sender"]


@pytest.mark.usefixtures("_authenticated_user")
@pytest.mark.usefixtures("_pgp_user")
@patch("hushline.email.smtplib.SMTP")
def test_update_smtp_settings_default_forwarding(
    SMTP: MagicMock, client: FlaskClient, user: User
) -> None:
    new_smtp_settings = {
        "email_address": "primary@example.com",
        "smtp_settings-smtp_encryption": "StartTLS",
        EmailForwardingForm.submit.name: "",
    }

    response = client.post(
        url_for("settings.notifications"),
        data=new_smtp_settings,
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "SMTP settings updated successfully" in response.text

    SMTP.assert_not_called()
    SMTP.return_value.__enter__.return_value.starttls.assert_not_called()
    SMTP.return_value.__enter__.return_value.login.assert_not_called()

    updated_user = (
        db.session.scalars(db.select(Username).filter_by(_username=user.primary_username.username))
        .one()
        .user
    )
    assert updated_user.email == new_smtp_settings["email_address"]
    assert updated_user.smtp_server is None
    assert updated_user.smtp_port is None
    assert updated_user.smtp_username is None
    assert updated_user.smtp_password is None
    assert updated_user.smtp_encryption.value == SMTPEncryption.default().value
    assert updated_user.smtp_sender is None


@pytest.mark.usefixtures("_authenticated_user")
def test_add_alias(app: Flask, client: FlaskClient, user: User) -> None:
    app.config["ALIAS_MODE"] = AliasMode.ALWAYS

    alias_username = str(uuid4())[0:12]
    response = client.post(
        url_for("settings.aliases"),
        data=form_to_data(
            NewAliasForm(
                data={
                    "username": alias_username,
                }
            )
        ),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Alias created successfully" in response.text

    alias = db.session.scalars(db.select(Username).filter_by(_username=alias_username)).one()
    assert not alias.is_primary
    assert alias.user_id == user.id


@pytest.mark.usefixtures("_authenticated_user")
def test_add_alias_fails_when_alias_mode_is_never(
    app: Flask, client: FlaskClient, user: User
) -> None:
    app.config["ALIAS_MODE"] = AliasMode.NEVER

    alias_username = str(uuid4())[0:12]
    response = client.post(
        url_for("settings.aliases"),
        data=form_to_data(
            NewAliasForm(
                data={
                    "username": alias_username,
                }
            )
        ),
        follow_redirects=True,
    )
    assert response.status_code == 400
    assert not db.session.scalars(
        db.select(Username).filter_by(_username=alias_username)
    ).one_or_none()


@pytest.mark.usefixtures("_authenticated_user")
def test_add_alias_not_exceed_max(
    app: Flask, client: FlaskClient, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    # patch to a small number for faster tests
    monkeypatch.setattr(User, "_PREMIUM_ALIAS_COUNT", 2)

    app.config["ALIAS_MODE"] = AliasMode.PREMIUM
    user.set_business_tier()

    # add up to the max number
    max_alises = user.max_aliases
    for _ in range(max_alises):
        alias_username = str(uuid4())[0:12]
        with app.test_request_context():
            form = NewAliasForm(
                data={
                    "username": alias_username,
                }
            )

        response = client.post(
            url_for("settings.aliases"),
            data=form_to_data(form),
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert db.session.scalars(
            db.select(Username).filter_by(_username=alias_username)
        ).one_or_none()

    # try adding one more
    alias_username = str(uuid4())[0:12]
    response = client.post(
        url_for("settings.aliases"),
        data=form_to_data(
            NewAliasForm(
                data={
                    "username": alias_username,
                }
            )
        ),
        follow_redirects=True,
    )
    assert response.status_code == 400
    assert (
        "Your current subscription level does not allow the creation of more aliases"
        in response.text
    )
    assert not db.session.scalars(
        db.select(Username).filter_by(_username=alias_username)
    ).one_or_none()


@pytest.mark.usefixtures("_authenticated_user")
def test_add_alias_duplicate(client: FlaskClient, user: User) -> None:
    response = client.post(
        url_for("settings.aliases"),
        data=form_to_data(
            NewAliasForm(
                data={
                    "username": user.primary_username.username,
                }
            )
        ),
        follow_redirects=True,
    )
    assert "This username is already taken." in response.text
    assert db.session.scalar(db.func.count(Username.id)) == 1


@pytest.mark.usefixtures("_authenticated_user")
def test_alias_page_loads(client: FlaskClient, user: User, user_alias: Username) -> None:
    response = client.get(
        url_for("settings.alias", username_id=user_alias.id), follow_redirects=True
    )
    assert response.status_code == 200
    assert f"Alias: @{user_alias.username}" in response.text


@pytest.mark.usefixtures("_authenticated_user")
def test_delete_account(
    client: FlaskClient, user: User, message: Message, authentication_log: AuthenticationLog
) -> None:
    # save these because SqlAlchemy is too smart about nullifying them on deletion
    user_id = user.id
    username_id = user.primary_username.id
    msg_id = message.id

    resp = client.post(url_for("settings.delete_account"), follow_redirects=True)
    assert resp.status_code == 200
    assert "Your account and all related information have been deleted." in resp.text
    assert db.session.scalars(db.select(User).filter_by(id=user_id)).one_or_none() is None
    assert db.session.scalars(db.select(Username).filter_by(id=username_id)).one_or_none() is None
    assert db.session.scalars(db.select(Message).filter_by(id=msg_id)).one_or_none() is None


@pytest.mark.usefixtures("_authenticated_user")
def test_alias_change_display_name(client: FlaskClient, user: User, user_alias: Username) -> None:
    new_display_name = (user_alias.display_name or "") + "_NEW"

    response = client.post(
        url_for("settings.alias", username_id=user_alias.id),
        data={
            "display_name": new_display_name,
            "update_display_name": "",  # html form
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Display name updated successfully" in response.text

    updated_user = db.session.scalars(
        db.select(Username).filter_by(_username=user_alias.username)
    ).one()
    assert updated_user.display_name == new_display_name


@pytest.mark.usefixtures("_authenticated_user")
def test_change_bio(client: FlaskClient, user: User) -> None:
    data = {
        "bio": str(uuid4()),
        "update_bio": "",  # html form
    }

    for i in range(1, 5):
        data[f"extra_field_label{i}"] = str(uuid4())
        data[f"extra_field_value{i}"] = str(uuid4())

    response = client.post(
        url_for("settings.profile"),
        data=data,
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Bio and fields updated successfully" in response.text

    updated_user = db.session.scalars(
        db.select(Username).filter_by(_username=user.primary_username.username)
    ).one()
    assert updated_user.bio == data["bio"]

    for i in range(1, 5):
        label = f"extra_field_label{i}"
        value = f"extra_field_value{i}"
        assert getattr(updated_user, label) == data[label]
        assert getattr(updated_user, value) == data[value]


@pytest.mark.usefixtures("_authenticated_user")
def test_alias_change_bio(client: FlaskClient, user: User, user_alias: Username) -> None:
    data = {
        "bio": str(uuid4()),
        "update_bio": "",  # html form
    }

    for i in range(1, 5):
        data[f"extra_field_label{i}"] = str(uuid4())
        data[f"extra_field_value{i}"] = str(uuid4())

    response = client.post(
        url_for("settings.alias", username_id=user_alias.id),
        data=data,
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Bio and fields updated successfully" in response.text

    updated_user = db.session.scalars(
        db.select(Username).filter_by(_username=user_alias.username)
    ).one()
    assert updated_user.bio == data["bio"]

    for i in range(1, 5):
        label = f"extra_field_label{i}"
        value = f"extra_field_value{i}"
        assert getattr(updated_user, label) == data[label]
        assert getattr(updated_user, value) == data[value]


@pytest.mark.usefixtures("_authenticated_user")
def test_change_directory_visibility(client: FlaskClient, user: User) -> None:
    original_visibility = user.primary_username.show_in_directory
    resp = client.post(
        url_for("settings.profile"),
        data={
            "show_in_directory": not original_visibility,
            "update_directory_visibility": "",  # html form
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Directory visibility updated successfully" in resp.text
    assert db.session.scalar(
        db.select(Username.show_in_directory).filter_by(id=user.primary_username.id)
    )


@pytest.mark.usefixtures("_authenticated_user")
def test_alias_change_directory_visibility(
    client: FlaskClient, user: User, user_alias: Username
) -> None:
    original_visibility = user_alias.show_in_directory
    resp = client.post(
        url_for("settings.alias", username_id=user_alias.id),
        data={
            "show_in_directory": not original_visibility,
            "update_directory_visibility": "",  # html form
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Directory visibility updated successfully" in resp.text
    assert db.session.scalar(db.select(Username.show_in_directory).filter_by(id=user_alias.id))


@pytest.mark.usefixtures("_authenticated_admin")
def test_update_brand_primary_color(client: FlaskClient, admin: User) -> None:
    color = "#acab00"
    resp = client.post(
        url_for("settings.branding"),
        data=form_to_data(UpdateBrandPrimaryColorForm(data={"brand_primary_hex_color": color})),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Brand primary color updated successfully" in resp.text

    soup = BeautifulSoup(resp.text, "html.parser")
    styles = soup.find_all("style")
    assert styles  # sensibility check
    for style in styles:
        if f"--color-brand: oklch(from {color} l c h);" in style.string:
            break
    else:
        pytest.fail("Brand color CSS not updated in response <style>")

    setting = db.session.get(OrganizationSetting, OrganizationSetting.BRAND_PRIMARY_COLOR)
    assert setting is not None
    assert setting.value == color


@pytest.mark.usefixtures("_authenticated_admin")
def test_update_brand_app_name(client: FlaskClient, admin: User) -> None:
    name = "h4cK3rZ"
    resp = client.post(
        url_for("settings.branding"),
        data=form_to_data(UpdateBrandAppNameForm(data={"brand_app_name": name})),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Brand app name updated successfully" in resp.text

    soup = BeautifulSoup(resp.text, "html.parser")
    h1s = soup.select("header h1")
    assert h1s  # sensibility check
    for h1 in h1s:
        if name in h1.string:
            break
    else:
        pytest.fail("Brand name not updated in header <h1>")

    setting = db.session.get(OrganizationSetting, OrganizationSetting.BRAND_NAME)
    assert setting is not None
    assert setting.value == name


@pytest.mark.usefixtures("_authenticated_admin")
def test_update_brand_logo(client: FlaskClient, admin: User) -> None:
    # 1x1 pixel white png
    png = b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQIW2P4DwQACfsD/Z8fLAAAAAAASUVORK5CYII="
    )

    resp = client.post(
        url_for("settings.branding"),
        data={
            "logo": (BytesIO(png), "wat.png"),
            UpdateBrandLogoForm.submit.name: "",
        },
        follow_redirects=True,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert "Brand logo updated successfully" in resp.text

    soup = BeautifulSoup(resp.text, "html.parser")
    imgs = soup.select("header img")
    assert imgs  # sensibility check
    logo_url = url_for("storage.public", path=OrganizationSetting.BRAND_LOGO_VALUE)
    for img in imgs:
        if img.attrs.get("src") == logo_url:
            break
    else:
        pytest.fail("Brand logo not updated in header <img>")

    setting = db.session.get(OrganizationSetting, OrganizationSetting.BRAND_LOGO)
    assert setting is not None
    assert setting.value == OrganizationSetting.BRAND_LOGO_VALUE

    # check the file got uploaded and is accessible
    resp = client.get(logo_url, follow_redirects=True)
    assert resp.status_code == 200
    assert resp.data == png

    resp = client.post(
        url_for("settings.branding"),
        data=form_to_data(DeleteBrandLogoForm()),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Brand logo deleted" in resp.text

    # check the file is not accessible
    resp = client.get(logo_url, follow_redirects=True)
    # yes this check is ridiculous. why? because we redirect not-founds instead of actually 404-ing
    assert "That page doesn" in resp.text


@pytest.mark.usefixtures("_authenticated_admin")
def test_enable_disable_guidance(client: FlaskClient, admin: User) -> None:
    # Enable guidance
    resp = client.post(
        url_for("settings.guidance"),
        data={
            "show_user_guidance": True,
            UserGuidanceForm.submit.name: "",
        },
        follow_redirects=True,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert "User guidance enabled" in resp.text

    # Check that it's enabled in org settings
    assert OrganizationSetting.fetch_one(OrganizationSetting.GUIDANCE_ENABLED) is True

    # Check that the guidance settings are show
    resp = client.get(url_for("settings.guidance"))
    assert resp.status_code == 200
    assert "Prompt Content" in resp.text

    # Disable guidance
    resp = client.post(
        url_for("settings.guidance"),
        data={
            UserGuidanceForm.submit.name: "",
        },
        follow_redirects=True,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert "User guidance disabled" in resp.text

    # Check that it's disabled in org settings
    assert OrganizationSetting.fetch_one(OrganizationSetting.GUIDANCE_ENABLED) is False

    # Check that the guidance settings are not shown
    resp = client.get(url_for("settings.guidance"))
    assert resp.status_code == 200
    assert "Prompt Content" not in resp.text


@pytest.mark.usefixtures("_authenticated_admin")
def test_update_guidance_emergency_exit(client: FlaskClient, admin: User) -> None:
    # Enable guidance
    client.post(
        url_for("settings.guidance"),
        data={
            "show_user_guidance": True,
            UserGuidanceForm.submit.name: "",
        },
        follow_redirects=True,
        content_type="multipart/form-data",
    )

    # Update exit button
    resp = client.post(
        url_for("settings.guidance"),
        data={
            "exit_button_text": "wikipedia!",
            "exit_button_link": "https://wikipedia.org",
            UserGuidanceEmergencyExitForm.submit.name: "",
        },
        follow_redirects=True,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert "Emergency exit button updated successfully" in resp.text

    # Check that it's updated in org settings
    assert (
        OrganizationSetting.fetch_one(OrganizationSetting.GUIDANCE_EXIT_BUTTON_TEXT) == "wikipedia!"
    )
    assert (
        OrganizationSetting.fetch_one(OrganizationSetting.GUIDANCE_EXIT_BUTTON_LINK)
        == "https://wikipedia.org"
    )


@pytest.mark.usefixtures("_authenticated_admin")
def test_update_guidance_emergency_exit_requires_url(client: FlaskClient, admin: User) -> None:
    # Enable guidance
    client.post(
        url_for("settings.guidance"),
        data={
            "show_user_guidance": True,
            UserGuidanceForm.submit.name: "",
        },
        follow_redirects=True,
        content_type="multipart/form-data",
    )

    # Update exit button with invalid link
    resp = client.post(
        url_for("settings.guidance"),
        data={
            "exit_button_text": "foo",
            "exit_button_link": "bar",
            UserGuidanceEmergencyExitForm.submit.name: "",
        },
        follow_redirects=True,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400

    # Check that it's not updated in org settings
    assert OrganizationSetting.fetch_one(OrganizationSetting.GUIDANCE_EXIT_BUTTON_TEXT) != "foo"
    assert OrganizationSetting.fetch_one(OrganizationSetting.GUIDANCE_EXIT_BUTTON_LINK) != "bar"


@pytest.mark.usefixtures("_authenticated_admin")
def test_update_guidance_prompts(client: FlaskClient, admin: User) -> None:
    # Enable guidance
    client.post(
        url_for("settings.guidance"),
        data={
            "show_user_guidance": True,
            UserGuidanceForm.submit.name: "",
        },
        follow_redirects=True,
        content_type="multipart/form-data",
    )

    # Update the first prompt
    resp = client.post(
        url_for("settings.guidance"),
        data={
            "heading_text": "prompt 1",
            "prompt_text": "prompt 1",
            "index": 0,
            UserGuidancePromptContentForm.submit.name: "",
        },
        follow_redirects=True,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200

    # Check that it's updated in org settings
    assert len(OrganizationSetting.fetch_one(OrganizationSetting.GUIDANCE_PROMPTS)) == 1

    # Add a new prompt
    resp = client.post(
        url_for("settings.guidance"),
        data={
            UserGuidanceAddPromptForm.submit.name: "",
        },
        follow_redirects=True,
        content_type="multipart/form-data",
    )

    # Check that it's updated in org settings
    assert len(OrganizationSetting.fetch_one(OrganizationSetting.GUIDANCE_PROMPTS)) == 2

    # Update the second prompt
    resp = client.post(
        url_for("settings.guidance"),
        data={
            "heading_text": "prompt 2",
            "prompt_text": "prompt 2",
            "index": 1,
            UserGuidancePromptContentForm.submit.name: "",
        },
        follow_redirects=True,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200

    # Add a new prompt
    resp = client.post(
        url_for("settings.guidance"),
        data={
            UserGuidanceAddPromptForm.submit.name: "",
        },
        follow_redirects=True,
        content_type="multipart/form-data",
    )

    # Update the third prompt
    resp = client.post(
        url_for("settings.guidance"),
        data={
            "heading_text": "prompt 3",
            "prompt_text": "prompt 3",
            "index": 2,
            UserGuidancePromptContentForm.submit.name: "",
        },
        follow_redirects=True,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200

    # Check that it's updated in org settings
    prompts = OrganizationSetting.fetch_one(OrganizationSetting.GUIDANCE_PROMPTS)
    assert len(prompts) == 3
    assert prompts[0]["heading_text"] == "prompt 1"
    assert prompts[1]["heading_text"] == "prompt 2"
    assert prompts[2]["heading_text"] == "prompt 3"

    # Delete the second prompt
    resp = client.post(
        url_for("settings.guidance"),
        data={
            "index": 1,
            UserGuidancePromptContentForm.delete_submit.name: "",
        },
        follow_redirects=True,
        content_type="multipart/form-data",
    )

    # Check that it's updated in org settings
    prompts = OrganizationSetting.fetch_one(OrganizationSetting.GUIDANCE_PROMPTS)
    assert len(prompts) == 2
    assert prompts[0]["heading_text"] == "prompt 1"
    assert prompts[1]["heading_text"] == "prompt 3"


@pytest.mark.usefixtures("_authenticated_admin")
def test_diretory_intro_text(client: FlaskClient, admin: User) -> None:
    alert = "<script>alert(1)</stript>"
    uuid = str(uuid4())
    data = alert + " " + uuid
    resp = client.post(
        url_for("settings.branding"),
        data={
            "markdown": data,
            UpdateDirectoryTextForm.submit.name: "",
        },
    )
    assert resp.status_code == 200
    assert "Directory intro text updated" in resp.text

    val = OrganizationSetting.fetch_one(OrganizationSetting.DIRECTORY_INTRO_TEXT)
    assert val == data

    resp = client.get(url_for("directory"))
    assert resp.status_code == 200
    assert uuid in resp.text
    assert alert not in resp.text
    assert "&lt;script&gt;" in resp.text


@pytest.mark.usefixtures("_authenticated_admin")
def test_homepage_user(client: FlaskClient, user: User, admin: User) -> None:
    resp = client.post(
        url_for("settings.branding"),
        data={
            "username": user.primary_username.username,
            SetHomepageUsernameForm.submit.name: "",
        },
    )
    assert resp.status_code == 200
    assert "Homepage set to user " in resp.text

    assert (
        OrganizationSetting.fetch_one(OrganizationSetting.HOMEPAGE_USER_NAME)
        == user.primary_username.username
    )

    # "log out" the user
    with client.session_transaction() as session:
        session.clear()

    resp = client.get(url_for("index"))
    assert resp.status_code == 302
    assert resp.headers["Location"] == url_for("profile", username=user.primary_username.username)

    # "log in" the user to make the change
    with client.session_transaction() as session:
        session["user_id"] = admin.id
        session["username"] = admin.primary_username.username
        session["is_authenticated"] = True

    resp = client.post(
        url_for("settings.branding"),
        data={
            SetHomepageUsernameForm.delete_submit.name: "",
        },
    )
    assert resp.status_code == 200, resp.text
    assert "Homepage reset to default" in resp.text

    assert OrganizationSetting.fetch_one(OrganizationSetting.HOMEPAGE_USER_NAME) is None

    # "log out" the user
    with client.session_transaction() as session:
        session.clear()

    resp = client.get(url_for("index"))
    assert resp.status_code == 302
    assert resp.headers["Location"] == url_for("directory")


@pytest.mark.usefixtures("_authenticated_admin")
def test_update_profile_header(client: FlaskClient, admin: User) -> None:
    template_str = "{{ display_name_or_username }} {{ display_name }} {{ username }}"
    resp = client.post(
        url_for("settings.branding"),
        data={
            "template": template_str,
            UpdateProfileHeaderForm.submit.name: "",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Profile header template updated successfully" in resp.text
    assert (
        OrganizationSetting.fetch_one(OrganizationSetting.BRAND_PROFILE_HEADER_TEMPLATE)
        == template_str
    )

    resp = client.post(
        url_for("settings.branding"),
        data={
            "template": "{{ INVALID SYNAX AHHHH !!!! }}",
            UpdateProfileHeaderForm.submit.name: "",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 400
    assert "Your submitted form could not be processed" in resp.text
    assert (
        OrganizationSetting.fetch_one(OrganizationSetting.BRAND_PROFILE_HEADER_TEMPLATE)
        == template_str
    )

    resp = client.post(
        url_for("settings.branding"),
        data={
            "template": "",
            UpdateProfileHeaderForm.submit.name: "",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Profile header template reset to default" in resp.text
    assert (
        db.session.scalars(
            db.select(OrganizationSetting).filter_by(
                key=OrganizationSetting.BRAND_PROFILE_HEADER_TEMPLATE
            )
        ).one_or_none()
        is None
    )
