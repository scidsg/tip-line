from typing import Optional, Tuple

from flask import (
    Blueprint,
    current_app,
    flash,
    render_template,
    request,
    session,
)
from flask_wtf import FlaskForm
from werkzeug.wrappers.response import Response
from wtforms import BooleanField, SubmitField
from wtforms.validators import Optional as OptionalField

from hushline.auth import authentication_required
from hushline.db import db
from hushline.email import create_smtp_config
from hushline.forms import DisplayNoneButton
from hushline.model import SMTPEncryption, User
from hushline.settings.common import form_error, set_input_disabled
from hushline.settings.forms import EmailForwardingForm
from hushline.utils import redirect_to_self


class ToggleNotificationsForm(FlaskForm):
    enable_email_notifications = BooleanField("Tip Notifications", validators=[OptionalField()])
    submit = SubmitField("Submit", name="toggle_notifications", widget=DisplayNoneButton())


class ToggleIncludeContentForm(FlaskForm):
    include_content = BooleanField("Include Message Contents", validators=[OptionalField()])
    submit = SubmitField("Submit", name="toggle_include_content", widget=DisplayNoneButton())


def handle_email_forwarding_form(
    user: User, form: EmailForwardingForm, default_forwarding_enabled: bool
) -> Optional[Response]:
    if form.email_address.data and not user.pgp_key:
        flash("⛔️ Email forwarding requires a configured PGP key")
        return None

    forwarding_enabled = form.custom_smtp_settings.data
    custom_smtp_settings = forwarding_enabled and (
        form.custom_smtp_settings.data or not default_forwarding_enabled
    )

    if custom_smtp_settings:
        try:
            smtp_config = create_smtp_config(
                form.smtp_settings.smtp_username.data,
                form.smtp_settings.smtp_server.data,
                form.smtp_settings.smtp_port.data,
                form.smtp_settings.smtp_password.data or user.smtp_password or "",
                form.smtp_settings.smtp_sender.data,
                encryption=SMTPEncryption[form.smtp_settings.smtp_encryption.data],
            )
            with smtp_config.smtp_login():
                pass
        except Exception as e:
            current_app.logger.debug(e)
            flash("⛔️ Unable to validate SMTP connection settings")
            return None

    user.email = form.email_address.data
    user.smtp_server = form.smtp_settings.smtp_server.data if custom_smtp_settings else None
    user.smtp_port = form.smtp_settings.smtp_port.data if custom_smtp_settings else None
    user.smtp_username = form.smtp_settings.smtp_username.data if custom_smtp_settings else None

    # Since passwords aren't pre-populated in the form, don't unset it if not provided
    user.smtp_password = (
        form.smtp_settings.smtp_password.data
        if custom_smtp_settings and form.smtp_settings.smtp_password.data
        else user.smtp_password
    )
    user.smtp_sender = (
        form.smtp_settings.smtp_sender.data
        if custom_smtp_settings and form.smtp_settings.smtp_sender.data
        else None
    )
    user.smtp_encryption = (
        form.smtp_settings.smtp_encryption.data
        if custom_smtp_settings
        else SMTPEncryption.default()
    )

    db.session.commit()
    flash("👍 SMTP settings updated successfully")
    return redirect_to_self()


def register_notifications_routes(bp: Blueprint) -> None:
    @bp.route("/email", methods=["GET", "POST"])
    @authentication_required
    def notifications() -> Response | Tuple[str, int]:
        user = db.session.scalars(db.select(User).filter_by(id=session["user_id"])).one()
        default_forwarding_enabled = bool(current_app.config.get("NOTIFICATIONS_ADDRESS"))

        toggle_notifications_form = ToggleNotificationsForm()
        toggle_include_content_form = ToggleIncludeContentForm()

        email_forwarding_form = EmailForwardingForm(
            data=dict(
                email_address=user.email,
                custom_smtp_settings=user.smtp_server or None,
            )
        )

        status_code = 200
        if request.method == "POST":
            if (
                toggle_notifications_form.submit.name in request.form
                and toggle_notifications_form.validate()
            ):
                user.enable_email_notifications = (
                    toggle_notifications_form.enable_email_notifications.data
                )
                db.session.commit()
                if toggle_notifications_form.enable_email_notifications.data:
                    flash("Email notifications enabled")
                else:
                    flash("Email notifications disabled")
                return redirect_to_self()
            elif (
                toggle_include_content_form.submit.name in request.form
                and toggle_include_content_form.validate()
            ):
                user.email_include_message_content = (
                    toggle_include_content_form.include_content.data
                )
                db.session.commit()
                if toggle_include_content_form.include_content.data:
                    flash("Email message content enabled")
                else:
                    flash("Email message content disabled")
                return redirect_to_self()
            elif (
                email_forwarding_form.submit.name in request.form
                and email_forwarding_form.validate()
                and (
                    resp := handle_email_forwarding_form(
                        user, email_forwarding_form, default_forwarding_enabled
                    )
                )
            ):
                return resp
            else:
                form_error()
                status_code = 400
        else:
            # we have to manually populate this because of subforms.
            # only when request isn't a POST so that failed submissions can be easily recreated
            email_forwarding_form.forwarding_enabled.data = user.email is not None
            if not user.pgp_key:
                set_input_disabled(email_forwarding_form.forwarding_enabled)
            email_forwarding_form.custom_smtp_settings.data = user.smtp_server is not None
            email_forwarding_form.smtp_settings.smtp_server.data = user.smtp_server
            email_forwarding_form.smtp_settings.smtp_port.data = user.smtp_port
            email_forwarding_form.smtp_settings.smtp_username.data = user.smtp_username
            email_forwarding_form.smtp_settings.smtp_encryption.data = user.smtp_encryption.value
            email_forwarding_form.smtp_settings.smtp_sender.data = user.smtp_sender

        toggle_notifications_form.enable_email_notifications.data = user.enable_email_notifications
        toggle_include_content_form.include_content.data = user.email_include_message_content

        return render_template(
            "settings/notifications.html",
            user=user,
            default_forwarding_enabled=default_forwarding_enabled,
            toggle_notifications_form=toggle_notifications_form,
            toggle_include_content_form=toggle_include_content_form,
            email_forwarding_form=email_forwarding_form,
        ), status_code
