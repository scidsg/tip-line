from typing import Tuple

from flask import (
    Blueprint,
    abort,
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
    Tier,
    User,
)
from hushline.settings.common import (
    build_field_forms,
    create_profile_forms,
    handle_field_post,
    handle_profile_post,
)


def register_profile_routes(bp: Blueprint) -> None:
    @bp.route("/profile", methods=["GET", "POST"])
    @authentication_required
    async def profile() -> Response | Tuple[str, int]:
        user = db.session.scalars(db.select(User).filter_by(id=session["user_id"])).one()
        username = user.primary_username

        if username is None:
            raise Exception("Username was unexpectedly none")

        display_name_form, directory_visibility_form, profile_form = create_profile_forms(username)
        field_forms, new_field_form = build_field_forms(username)

        status_code = 200
        if request.method == "POST":
            res = await handle_profile_post(
                display_name_form, directory_visibility_form, profile_form, username
            )
            if res:
                return res

            status_code = 400

        business_tier = Tier.business_tier()
        business_tier_display_price = ""
        if business_tier:
            price_usd = business_tier.monthly_amount / 100
            if price_usd % 1 == 0:
                business_tier_display_price = str(int(price_usd))
            else:
                business_tier_display_price = f"{price_usd:.2f}"

        return render_template(
            "settings/profile.html",
            user=user,
            username=username,
            display_name_form=display_name_form,
            directory_visibility_form=directory_visibility_form,
            profile_form=profile_form,
            field_forms=field_forms,
            new_field_form=new_field_form,
            business_tier_display_price=business_tier_display_price,
        ), status_code

    @bp.route("/profile/fields", methods=["GET", "POST"])
    @authentication_required
    def profile_fields() -> Response | Tuple[str, int]:
        user = db.session.scalars(db.select(User).filter_by(id=session["user_id"])).one()

        if not user.fields_enabled:
            return abort(401)

        username = user.primary_username

        if username is None:
            raise Exception("Username not found")

        username.create_default_field_defs()

        if request.method == "POST":
            res = handle_field_post(username)
            if res:
                return res

        return redirect(url_for(".profile"))
