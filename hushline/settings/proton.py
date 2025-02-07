import requests
from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    session,
    url_for,
)
from werkzeug.wrappers.response import Response

from hushline.auth import authentication_required
from hushline.db import db
from hushline.model import User
from hushline.settings.common import is_valid_pgp_key
from hushline.settings.forms import PGPProtonForm


def register_proton_routes(bp: Blueprint) -> None:
    @bp.route("/update_pgp_key_proton", methods=["POST"])
    @authentication_required
    def update_pgp_key_proton() -> Response | str:
        user = db.session.scalars(db.select(User).filter_by(id=session["user_id"])).one()
        form = PGPProtonForm()

        if not form.validate_on_submit():
            flash("⛔️ Invalid email address.")
            return redirect(url_for(".index"))

        email = form.email.data

        # Try to fetch the PGP key from ProtonMail
        try:
            resp = requests.get(
                # TODO email needs to be URL escaped
                f"https://mail-api.proton.me/pks/lookup?op=get&search={email}",
                timeout=5,
            )
        except requests.exceptions.RequestException as e:
            current_app.logger.error(f"Error fetching PGP key from Proton Mail: {e}")
            flash("⛔️ Error fetching PGP key from Proton Mail.")
            return redirect(url_for(".notifications"))

        if resp.status_code == 200:  # noqa: PLR2004
            pgp_key = resp.text
            if is_valid_pgp_key(pgp_key):
                user.pgp_key = pgp_key
                db.session.commit()
            else:
                flash("⛔️ No PGP key found for the email address.")
                return redirect(url_for(".notifications"))
        else:
            flash("⛔️ This isn't a Proton Mail email address.")
            return redirect(url_for(".notifications"))

        flash("👍 PGP key updated successfully.")
        return redirect(url_for(".notifications"))
