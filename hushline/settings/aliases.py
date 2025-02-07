from typing import Tuple

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.wrappers.response import Response

from hushline.auth import authentication_required
from hushline.db import db
from hushline.model import (
    User,
    Username,
)
from hushline.settings.common import (
    build_field_forms,
    create_profile_forms,
    form_error,
    handle_field_post,
    handle_new_alias_form,
    handle_profile_post,
)
from hushline.settings.forms import (
    NewAliasForm,
)


def register_aliases_routes(bp: Blueprint) -> None:
    @bp.route("/aliases", methods=["GET", "POST"])
    @authentication_required
    def aliases() -> Response | Tuple[str, int]:
        user = db.session.scalars(db.select(User).filter_by(id=session["user_id"])).one()
        new_alias_form = NewAliasForm()

        status_code = 200
        if request.method == "POST":
            if new_alias_form.validate() and (resp := handle_new_alias_form(user, new_alias_form)):
                return resp
            else:
                form_error()
                status_code = 400

        aliases = db.session.scalars(
            db.select(Username)
            .filter_by(is_primary=False, user_id=user.id)
            .order_by(db.func.coalesce(Username._display_name, Username._username))
        ).all()

        return render_template(
            "settings/aliases.html",
            user=user,
            aliases=aliases,
            new_alias_form=new_alias_form,
        ), status_code

    @bp.route("/alias/<int:username_id>", methods=["GET", "POST"])
    @authentication_required
    async def alias(username_id: int) -> Response | Tuple[str, int]:
        alias = db.session.scalars(
            db.select(Username).filter_by(
                id=username_id, user_id=session["user_id"], is_primary=False
            )
        ).one_or_none()
        if not alias:
            flash("Alias not found.")
            return redirect(url_for(".index"))

        display_name_form, directory_visibility_form, profile_form = create_profile_forms(alias)
        field_forms, new_field_form = build_field_forms(alias)

        status_code = 200
        if request.method == "POST":
            res = await handle_profile_post(
                display_name_form, directory_visibility_form, profile_form, alias
            )
            if res:
                return res

            status_code = 400

        return render_template(
            "settings/alias.html",
            user=alias.user,
            alias=alias,
            display_name_form=display_name_form,
            directory_visibility_form=directory_visibility_form,
            profile_form=profile_form,
            field_forms=field_forms,
            new_field_form=new_field_form,
        ), status_code

    @bp.route("/alias/<int:username_id>/fields", methods=["GET", "POST"])
    @authentication_required
    def alias_fields(username_id: int) -> Response | Tuple[str, int]:
        alias = db.session.scalars(
            db.select(Username).filter_by(
                id=username_id, user_id=session["user_id"], is_primary=False
            )
        ).one_or_none()
        if not alias:
            flash("Alias not found.")
            return redirect(url_for(".index"))

        if not alias.user.fields_enabled:
            return abort(401)

        alias.create_default_field_defs()

        if request.method == "POST":
            res = handle_field_post(alias)
            if res:
                return res

        return redirect(url_for(".alias", username_id=alias.id))
