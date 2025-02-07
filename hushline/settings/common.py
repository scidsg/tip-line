import asyncio
from hmac import compare_digest as bytes_are_equal
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup
from flask import (
    current_app,
    flash,
    redirect,
    request,
    session,
    url_for,
)
from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError
from werkzeug.wrappers.response import Response
from wtforms import Field

from hushline.crypto import is_valid_pgp_key
from hushline.db import db
from hushline.model import (
    FieldDefinition,
    FieldType,
    FieldValue,
    User,
    Username,
)
from hushline.settings.forms import (
    ChangePasswordForm,
    ChangeUsernameForm,
    DirectoryVisibilityForm,
    DisplayNameForm,
    FieldChoiceForm,
    FieldForm,
    NewAliasForm,
    PGPKeyForm,
    ProfileForm,
)
from hushline.utils import redirect_to_self


def form_error() -> None:
    flash("Your submitted form could not be processed.")


def set_field_attribute(input_field: Field, attribute: str, value: str) -> None:
    if input_field.render_kw is None:
        input_field.render_kw = {}
    input_field.render_kw[attribute] = value


def unset_field_attribute(input_field: Field, attribute: str) -> None:
    if input_field.render_kw is not None:
        input_field.render_kw.pop(attribute)


def set_input_disabled(input_field: Field, disabled: bool = True) -> None:
    """
    disable the given input

    Args:
        inputField(Input): the WTForms input to disable
        disabled(bool): if true set the disabled attribute of the input
    """
    if disabled:
        set_field_attribute(input_field, "disabled", "disabled")
    else:
        unset_field_attribute(input_field, "disabled")


# Define the async function for URL verification
async def verify_url(
    session: aiohttp.ClientSession, username: Username, i: int, url_to_verify: str, profile_url: str
) -> None:
    try:
        async with session.get(url_to_verify, timeout=aiohttp.ClientTimeout(total=5)) as response:
            response.raise_for_status()
            html_content = await response.text()

            soup = BeautifulSoup(html_content, "html.parser")
            verified = False
            for link in soup.find_all("a"):
                href = link.get("href")
                rel = link.get("rel", [])
                if href == profile_url and "me" in rel:
                    verified = True
                    break

            setattr(username, f"extra_field_verified{i}", verified)
    except aiohttp.ClientError as e:
        current_app.logger.error(f"Error fetching URL for field {i}: {e}")
        setattr(username, f"extra_field_verified{i}", False)


async def handle_update_bio(username: Username, form: ProfileForm) -> Response:
    username.bio = form.bio.data.strip()

    # Define base_url from the environment or config
    profile_url = url_for("profile", _external=True, username=username._username)

    async with aiohttp.ClientSession() as client_session:
        tasks = []
        for i in range(1, 5):
            if (label_field := getattr(form, f"extra_field_label{i}", None)) and (
                label := getattr(label_field, "data", None)
            ):
                setattr(username, f"extra_field_label{i}", label)

            if (value_field := getattr(form, f"extra_field_value{i}", None)) and (
                value := getattr(value_field, "data", None)
            ):
                setattr(username, f"extra_field_value{i}", value)
            else:
                setattr(username, f"extra_field_verified{i}", False)
                continue

            # Verify the URL only if it starts with "https://"
            if value.startswith("https://"):
                task = verify_url(client_session, username, i, value, profile_url)
                tasks.append(task)

        # Run all the tasks concurrently
        if tasks:  # Only gather if there are tasks to run
            await asyncio.gather(*tasks)

    db.session.commit()
    flash("👍 Bio and fields updated successfully.")
    return redirect_to_self()


def handle_update_directory_visibility(user: Username, form: DirectoryVisibilityForm) -> Response:
    user.show_in_directory = form.show_in_directory.data
    db.session.commit()
    flash("👍 Directory visibility updated successfully.")
    return redirect_to_self()


def handle_display_name_form(username: Username, form: DisplayNameForm) -> Response:
    username.display_name = (form.display_name.data or "").strip() or None
    db.session.commit()

    flash("👍 Display name updated successfully.")
    current_app.logger.debug(
        f"Display name updated to {username.display_name}, "
        f"Verification status: {username.is_verified}"
    )
    return redirect_to_self()


def handle_change_username_form(username: Username, form: ChangeUsernameForm) -> Response:
    new_username = form.new_username.data

    # TODO a better pattern would be to try to commit, catch the exception, and match
    # on the name of the unique index that errored
    if db.session.scalar(db.exists(Username).where(Username._username == new_username).select()):
        flash("💔 This username is already taken.")
    else:
        username.username = new_username
        db.session.commit()

        session["username"] = new_username
        flash("👍 Username changed successfully.")
        current_app.logger.debug(
            f"Username updated to {username.username}, "
            f"Verification status: {username.is_verified}"
        )
    return redirect_to_self()


def handle_new_alias_form(user: User, new_alias_form: NewAliasForm) -> Optional[Response]:
    current_app.logger.debug(f"Attempting to create alias for user_id={user.id}")

    count = db.session.scalar(
        db.select(
            db.func.count(Username.id).filter(
                Username.user_id == user.id, Username.is_primary.is_(False)
            )
        )
    )
    if count >= user.max_aliases:
        flash("Your current subscription level does not allow the creation of more aliases.")
        return None

    uname = Username(_username=new_alias_form.username.data, user_id=user.id, is_primary=False)
    db.session.add(uname)
    try:
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        if isinstance(e.orig, UniqueViolation) and '"uq_usernames_username"' in str(e.orig):
            flash("💔 This username is already taken.")
            return None
        current_app.logger.error("Error creating username", exc_info=True)
        flash("⛔️ Internal server error. Alias not created.")
        return None
    else:
        flash("👍 Alias created successfully.")

    uname.create_default_field_defs()

    return redirect_to_self()


def handle_change_password_form(
    user: User, change_password_form: ChangePasswordForm
) -> Optional[Response]:
    if not user.check_password(change_password_form.old_password.data):
        change_password_form.old_password.errors.append("Incorrect old password.")
        return None

    # SECURITY: only check equality after successful old password check
    if bytes_are_equal(
        change_password_form.old_password.data.encode(),
        change_password_form.new_password.data.encode(),
    ):
        change_password_form.new_password.errors.append("Cannot choose a repeat password.")
        return None

    user.password_hash = change_password_form.new_password.data
    db.session.commit()
    session.clear()
    flash("👍 Password successfully changed. Please log in again.", "success")
    return redirect(url_for("login"))


def handle_pgp_key_form(user: User, form: PGPKeyForm) -> Response:
    if not (pgp_key := (form.pgp_key.data or "").strip()):
        user.pgp_key = None
        user.email = None
        db.session.commit()
    elif is_valid_pgp_key(pgp_key):
        user.pgp_key = pgp_key
        db.session.commit()
    else:
        flash("⛔️ Invalid PGP key format or import failed.")
        return redirect(url_for(".encryption"))

    flash("👍 PGP key updated successfully.")
    return redirect(url_for(".encryption"))


def create_profile_forms(
    username: Username,
) -> tuple[DisplayNameForm, DirectoryVisibilityForm, ProfileForm]:
    display_name_form = DisplayNameForm(display_name=username.display_name)
    directory_visibility_form = DirectoryVisibilityForm(
        show_in_directory=username.show_in_directory
    )
    profile_form = ProfileForm(
        bio=username.bio or "",
        **{
            f"extra_field_label{i}": getattr(username, f"extra_field_label{i}", "")
            for i in range(1, 5)
        },
        **{
            f"extra_field_value{i}": getattr(username, f"extra_field_value{i}", "")
            for i in range(1, 5)
        },
    )
    return display_name_form, directory_visibility_form, profile_form


async def handle_profile_post(
    display_name_form: DisplayNameForm,
    directory_visibility_form: DirectoryVisibilityForm,
    profile_form: ProfileForm,
    username: Username,
) -> Response | None:
    """
    Handle the POST request for the profile page. Returns None on error.
    """
    if display_name_form.submit.name in request.form and display_name_form.validate():
        return handle_display_name_form(username, display_name_form)
    elif (
        directory_visibility_form.submit.name in request.form
        and directory_visibility_form.validate()
    ):
        return handle_update_directory_visibility(username, directory_visibility_form)
    elif profile_form.submit.name in request.form and profile_form.validate():
        return await handle_update_bio(username, profile_form)

    form_error()
    return None


def handle_field_post(username: Username) -> Response | None:
    field_form = FieldForm()
    if field_form.validate():
        # Create a new field
        if field_form.submit.name in request.form:
            # Get the highest existing sort_order for this username
            max_sort_order = (
                db.session.scalar(
                    db.select(db.func.coalesce(db.func.max(FieldDefinition.sort_order), 0)).filter(
                        FieldDefinition.username_id == username.id
                    )
                )
                or 0
            )

            field_definition = FieldDefinition(
                username,
                field_form.label.data,
                FieldType(field_form.field_type.data),
                field_form.required.data,
                field_form.enabled.data,
                field_form.encrypted.data,
                [c["choice"] for c in field_form.choices.data],
            )

            field_definition.sort_order = max_sort_order + 1  # Ensure unique ordering

            db.session.add(field_definition)
            db.session.commit()
            flash("New field added.")
            return redirect_to_self()

        # Update an existing field
        if field_form.update.name in request.form:
            current_app.logger.info("Updating field")
            field_definition = db.session.scalars(
                db.select(FieldDefinition).filter_by(id=int(field_form.id.data))
            ).one()
            field_definition.label = field_form.label.data
            field_definition.field_type = FieldType(field_form.field_type.data)
            field_definition.required = field_form.required.data
            field_definition.enabled = field_form.enabled.data
            field_definition.encrypted = field_form.encrypted.data
            field_definition.choices = [c["choice"] for c in field_form.choices.data]
            db.session.commit()
            flash("Field updated.")
            return redirect_to_self()

        # Delete a field
        if field_form.delete.name in request.form:
            current_app.logger.info("Deleting field")
            field_definition = db.session.scalars(
                db.select(FieldDefinition).filter_by(id=int(field_form.id.data))
            ).one()

            # Delete all field values that rely on this field definition
            db.session.execute(
                db.delete(FieldValue).filter_by(field_definition_id=field_definition.id)
            )

            db.session.delete(field_definition)
            db.session.commit()
            flash("Field deleted.")
            return redirect_to_self()

        # Move a field up
        if field_form.move_up.name in request.form:
            current_app.logger.info("Moving field up")
            field_definition = db.session.scalars(
                db.select(FieldDefinition).filter_by(id=int(field_form.id.data))
            ).one()
            field_definition.move_up()
            flash("Field moved up.")
            return redirect_to_self()

        # Move a field down
        if field_form.move_down.name in request.form:
            current_app.logger.info("Moving field down")
            field_definition = db.session.scalars(
                db.select(FieldDefinition).filter_by(id=int(field_form.id.data))
            ).one()
            field_definition.move_down()
            flash("Field moved down.")
            return redirect_to_self()

    return None


def build_field_forms(username: Username) -> tuple[list[FieldForm], FieldForm]:
    field_forms = []
    for field in username.message_fields:
        form = FieldForm(obj=field)
        form.field_type.data = field.field_type.value

        form.choices.entries = []
        for choice in field.choices:
            choice_form = FieldChoiceForm()
            choice_form.choice.data = choice
            form.choices.append_entry(choice_form.data)

        field_forms.append(form)

    new_field_form = FieldForm()

    return field_forms, new_field_form
