from typing import Tuple

from flask import (
    Blueprint,
    render_template,
    request,
    session,
)
from werkzeug.wrappers.response import Response

from hushline.auth import authentication_required
from hushline.db import db
from hushline.model import User
from hushline.settings.common import (
    form_error,
    handle_pgp_key_form,
)
from hushline.settings.forms import (
    PGPKeyForm,
    PGPProtonForm,
)


def register_encryption_routes(bp: Blueprint) -> None:
    @bp.route("/encryption", methods=["GET", "POST"])
    @authentication_required
    def encryption() -> Response | Tuple[str, int]:
        user = db.session.scalars(db.select(User).filter_by(id=session["user_id"])).one()

        pgp_proton_form = PGPProtonForm()
        pgp_key_form = PGPKeyForm(pgp_key=user.pgp_key)

        status_code = 200
        if request.method == "POST":
            if pgp_key_form.submit.name in request.form and pgp_key_form.validate():
                return handle_pgp_key_form(user, pgp_key_form)
            else:
                form_error()
                status_code = 400

        return render_template(
            "settings/encryption.html",
            user=user,
            pgp_proton_form=pgp_proton_form,
            pgp_key_form=pgp_key_form,
        ), status_code
