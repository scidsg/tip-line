import json
import os
from datetime import timedelta
from enum import Enum, unique
from json import JSONDecodeError
from typing import Any, Mapping, Optional, Self

from markupsafe import Markup

from hushline.utils import if_not_none, parse_bool

_STRING_CFG_PREFIX = "HL_CFG_"
_JSON_CFG_PREFIX = "HL_CFG_JSON_"


class ConfigParseError(Exception):
    pass


@unique
class AliasMode(Enum):
    ALWAYS = "always"
    PREMIUM = "premium"
    NEVER = "never"

    @classmethod
    def parse(cls, string: str) -> Self:
        for var in cls:
            if var.value == string:
                return var
        raise ConfigParseError(f"Not a valid value for {cls.__name__}: {string!r}")


@unique
class FieldsMode(Enum):
    ALWAYS = "always"
    PREMIUM = "premium"

    @classmethod
    def parse(cls, string: str) -> Self:
        for var in cls:
            if var.value == string:
                return var
        raise ConfigParseError(f"Not a valid value for {cls.__name__}: {string!r}")


def load_config(env: Optional[Mapping[str, str]] = None) -> Mapping[str, Any]:
    if env is None:
        env = os.environ

    config: dict[str, Any] = {}
    for func in [
        _load_flask,
        _load_sqlalchemy,
        _load_smtp,
        _load_stripe,
        _load_blob_storage,
        _load_hushline_misc,
        # load strings and JSON last as overrides
        _load_strings,
        _load_json,
    ]:
        config |= func(env)

    return config


def _load_flask(env: Mapping[str, str]) -> Mapping[str, Any]:
    data = {
        "SESSION_COOKIE_NAME": env.get("SESSION_COOKIE_NAME", "__HOST-session"),
        "SESSION_COOKIE_SECURE": True,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": timedelta(minutes=30),
    }

    # Handle the tips domain for profile verification
    if server_name := env.get("SERVER_NAME"):
        data["SERVER_NAME"] = server_name
    data["PREFERRED_URL_SCHEME"] = "https" if server_name else "http"

    for key in ["FLASK_ENV", "SECRET_KEY"]:
        if val := env.get(key):
            data[key] = val

    return data


def _load_sqlalchemy(env: Mapping[str, str]) -> Mapping[str, Any]:
    data: dict[str, Any] = {}

    if db_uri := env.get("SQLALCHEMY_DATABASE_URI"):
        # if it's a Postgres URI, replace the scheme with `postgresql+psycopg`
        # because we're using the psycopg driver
        if db_uri.startswith("postgresql://"):
            db_uri = db_uri.replace("postgresql://", "postgresql+psycopg://", 1)
        data["SQLALCHEMY_DATABASE_URI"] = db_uri

    data["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    return data


def _load_smtp(env: Mapping[str, str]) -> Mapping[str, Any]:
    data: dict[str, Any] = {}

    for key in ["NOTIFICATIONS_ADDRESS", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_SERVER"]:
        if val := env.get(key):
            data[key] = val

    data["SMTP_PORT"] = if_not_none(env.get("SMTP_PORT"), int, allow_falsey=False)
    data["SMTP_ENCRYPTION"] = env.get("SMTP_ENCRYPTION", "StartTLS")
    data["SMTP_FORWARDING_MESSAGE_HTML"] = if_not_none(
        env.get("SMTP_FORWARDING_MESSAGE_HTML"), Markup, allow_falsey=False
    )

    return data


def _load_hushline_misc(env: Mapping[str, str]) -> Mapping[str, Any]:
    data: dict[str, Any] = {}

    # these are required by the Flask app but not by the Stripe worker
    # so we have to allow for them to be missing
    if key := env.get("ENCRYPTION_KEY"):
        data["ENCRYPTION_KEY"] = key
    if key := env.get("SESSION_FERNET_KEY"):
        data["SESSION_FERNET_KEY"] = key

    if onion := env.get("ONION_HOSTNAME"):
        data["ONION_HOSTNAME"] = onion

    bool_configs = [
        ("DIRECTORY_VERIFIED_TAB_ENABLED", True),
        ("FILE_UPLOADS_ENABLED", False),
        ("REGISTRATION_CODES_REQUIRED", True),
    ]
    for key, default in bool_configs:
        if value := env.get(key):
            data[key] = parse_bool(value)
        else:
            data[key] = default

    if alias_str := env.get("ALIAS_MODE"):
        data["ALIAS_MODE"] = AliasMode.parse(alias_str)
    else:
        data["ALIAS_MODE"] = AliasMode.ALWAYS

    if fields_str := env.get("FIELDS_MODE"):
        data["FIELDS_MODE"] = FieldsMode.parse(fields_str)
    else:
        data["FIELDS_MODE"] = FieldsMode.ALWAYS

    return data


def _load_stripe(env: Mapping[str, str]) -> Mapping[str, Any]:
    data = {}

    for key in ["STRIPE_PUBLISHABLE_KEY", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"]:
        if (value := env.get(key)) and value != "":
            data[key] = value

    return data


def _load_blob_storage(env: Mapping[str, str]) -> Mapping[str, Any]:
    data = {}

    for k, v in env.items():
        if k.startswith("BLOB_STORAGE"):
            data[k] = v

    return data


def _load_strings(env: Mapping[str, str]) -> Mapping[str, Any]:
    return {
        k[len(_STRING_CFG_PREFIX) :]: v for k, v in env.items() if k.startswith(_STRING_CFG_PREFIX)
    }


def _load_json(env: Mapping[str, str]) -> Mapping[str, Any]:
    data = {}

    for k, v in env.items():
        if not k.startswith(_JSON_CFG_PREFIX):
            continue

        try:
            data[k[len(_JSON_CFG_PREFIX) :]] = json.loads(v)
        except JSONDecodeError:
            raise ConfigParseError(f"Env var {k!r} could not be parsed as JSON")

    return data
