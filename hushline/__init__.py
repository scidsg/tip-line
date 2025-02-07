import asyncio
import logging
from typing import Any, Mapping, Optional

from flask import Flask, flash, redirect, request, session, url_for
from flask.cli import AppGroup
from jinja2 import StrictUndefined
from werkzeug.wrappers.response import Response

from hushline import admin, premium, routes, settings, storage
from hushline.config import AliasMode, load_config
from hushline.db import db, migrate
from hushline.md import md_to_html
from hushline.model import OrganizationSetting, Tier, User
from hushline.secure_session import EncryptedSessionInterface
from hushline.storage import public_store
from hushline.version import __version__


def create_app(config: Optional[Mapping[str, Any]] = None) -> Flask:
    app = Flask(__name__)
    app.session_interface = EncryptedSessionInterface()
    app.logger.setLevel(logging.DEBUG)

    if not config:
        config = load_config()

    # hushline specific configs

    app.config.from_mapping(config)
    configure_jinja(app)
    db.init_app(app)
    migrate.init_app(app, db)
    public_store.init_app(app)

    routes.init_app(app)
    for module in [admin, settings, storage]:
        app.register_blueprint(module.create_blueprint())

    if app.config.get("STRIPE_SECRET_KEY"):
        app.register_blueprint(premium.create_blueprint(app))
        # Initialize Stripe
        with app.app_context():
            premium.init_stripe()

    @app.errorhandler(404)
    def page_not_found(e: Exception) -> Response:
        flash("⛓️‍💥 That page doesn't exist.", "warning")
        return redirect(url_for("index"))

    # Add Content-Security-Policy header to all responses
    @app.after_request
    def add_security_header(response: Response) -> Response:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self';"
            "script-src 'self' https://js.stripe.com https://cdn.jsdelivr.net 'wasm-unsafe-eval';"
            "img-src 'self' data: https:;"
            "style-src 'self' 'unsafe-inline';"
            "worker-src 'self' blob:;"
            "frame-ancestors 'none';"
            "connect-src 'self' https://api.stripe.com https://cdn.jsdelivr.net data:;"
            "child-src https://js.stripe.com;"
            "frame-src https://js.stripe.com;"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), midi=(), notifications=(), push=(), sync-xhr=(), microphone=(), camera=(), magnetometer=(), gyroscope=(), speaker=(), vibrate=(), fullscreen=(), payment=(), interest-cohort=();"  # noqa: E501
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # If SERVER_NAME does not end in .onion, add Strict-Transport-Security
        if not (app.config.get("SERVER_NAME") or "").endswith(".onion"):
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubdomains"

        return response

    # Add Onion-Location header to all responses
    if onion := app.config.get("ONION_HOSTNAME"):

        @app.after_request
        def add_onion_location_header(response: Response) -> Response:
            response.headers["Onion-Location"] = f"http://{onion}{request.path}"
            return response

    # Register custom CLI commands
    register_commands(app)

    return app


def configure_jinja(app: Flask) -> None:
    app.jinja_env.globals["hushline_version"] = __version__
    app.jinja_env.globals["AliasMode"] = AliasMode

    app.jinja_env.filters["markdown"] = md_to_html

    if app.config.get("FLASK_ENV") == "development":
        app.logger.info("Development environment detected, enabling jinja2.StrictUndefined")
        app.jinja_env.undefined = StrictUndefined

    # always pop the config to avoid accidentally dumping all our secrets to the user
    app.jinja_env.globals.pop("config", None)
    app.jinja_env.globals["smtp_forwarding_message_html"] = app.config[
        "SMTP_FORWARDING_MESSAGE_HTML"
    ]
    if onion_hostname := app.config.get("ONION_HOSTNAME"):
        app.jinja_env.globals["onion_hostname"] = onion_hostname

    @app.context_processor
    def inject_variables() -> dict[str, Any]:
        data = OrganizationSetting.fetch(
            OrganizationSetting.BRAND_NAME,
            OrganizationSetting.BRAND_PRIMARY_COLOR,
            OrganizationSetting.GUIDANCE_ENABLED,
            OrganizationSetting.GUIDANCE_EXIT_BUTTON_TEXT,
            OrganizationSetting.GUIDANCE_EXIT_BUTTON_LINK,
            OrganizationSetting.GUIDANCE_PROMPTS,
        )

        data.update(
            alias_mode=app.config["ALIAS_MODE"],
            fields_mode=app.config["FIELDS_MODE"],
            directory_verified_tab_enabled=app.config["DIRECTORY_VERIFIED_TAB_ENABLED"],
            is_onion_service=request.host.lower().endswith(".onion"),
            is_premium_enabled=bool(app.config.get("STRIPE_SECRET_KEY", False)),
        )

        if "user_id" in session:
            data["user"] = db.session.get(User, session["user_id"])

        return data

    @app.context_processor
    def inject_logo() -> dict[str, str | None]:
        val = None
        if setting := OrganizationSetting.fetch_one(OrganizationSetting.BRAND_LOGO):
            val = url_for("storage.public", path=setting)
        return {"brand_logo_url": val}


def register_commands(app: Flask) -> None:
    stripe_cli = AppGroup("stripe")

    @stripe_cli.command("configure")
    def configure() -> None:
        """Configure Stripe and premium tiers"""
        # Make sure tiers exist
        with app.app_context():
            free_tier = Tier.free_tier()
            if not free_tier:
                free_tier = Tier(name="Free", monthly_amount=0)
                db.session.add(free_tier)
                db.session.commit()
            business_tier = Tier.business_tier()
            if not business_tier:
                business_tier = Tier(name="Business", monthly_amount=2000)
                db.session.add(business_tier)
                db.session.commit()

        # Configure Stripe
        if app.config["STRIPE_SECRET_KEY"]:
            with app.app_context():
                premium.init_stripe()
                premium.create_products_and_prices()
        else:
            app.logger.info("Skipping Stripe configuration because STRIPE_SECRET_KEY is not set")

    @stripe_cli.command("start-worker")
    def start_worker() -> None:
        """Start the Stripe worker"""
        if not app.config["STRIPE_SECRET_KEY"]:
            app.logger.error("Cannot start the Stripe worker without a STRIPE_SECRET_KEY")
            return

        with app.app_context():
            asyncio.run(premium.worker(app))

    app.cli.add_command(stripe_cli)
