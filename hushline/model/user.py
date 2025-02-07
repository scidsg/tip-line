from typing import TYPE_CHECKING, Any, Optional

from flask import current_app
from passlib.hash import scrypt
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hushline.config import AliasMode, FieldsMode
from hushline.crypto import decrypt_field, encrypt_field
from hushline.db import db
from hushline.model.enums import SMTPEncryption, StripeSubscriptionStatusEnum
from hushline.model.tier import Tier

if TYPE_CHECKING:
    from flask_sqlalchemy.model import Model

    from hushline.model.message import Message
    from hushline.model.username import Username
else:
    Model = db.Model


class User(Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    is_admin: Mapped[bool] = mapped_column(default=False)
    _password_hash: Mapped[str] = mapped_column("password_hash", db.String(512))
    _totp_secret: Mapped[Optional[str]] = mapped_column("totp_secret", db.String(255))

    primary_username: Mapped["Username"] = relationship(
        primaryjoin="and_(Username.user_id == User.id, Username.is_primary)",
        back_populates="user",
    )
    messages: Mapped[list["Message"]] = relationship(
        secondary="usernames",
        primaryjoin="Username.user_id == User.id",
        secondaryjoin="Message.username_id == Username.id",
        order_by="Message.id.desc()",
        backref=db.backref("user", lazy=False, uselist=False, viewonly=True),
        lazy=True,
        uselist=True,
        viewonly=True,
    )

    _email: Mapped[Optional[str]] = mapped_column("email", db.String(255))
    _smtp_server: Mapped[Optional[str]] = mapped_column("smtp_server", db.String(255))
    smtp_port: Mapped[Optional[int]]
    _smtp_username: Mapped[Optional[str]] = mapped_column("smtp_username", db.String(255))
    _smtp_password: Mapped[Optional[str]] = mapped_column("smtp_password", db.String(255))
    _pgp_key: Mapped[Optional[str]] = mapped_column("pgp_key", db.Text)
    smtp_encryption: Mapped[SMTPEncryption] = mapped_column(
        db.Enum(SMTPEncryption, native_enum=False), default=SMTPEncryption.StartTLS
    )
    smtp_sender: Mapped[Optional[str]]

    # Paid tier fields
    tier_id: Mapped[int | None] = mapped_column(db.ForeignKey("tiers.id"), nullable=True)
    tier: Mapped["Tier"] = relationship(backref=db.backref("tiers", lazy=True))

    stripe_customer_id = mapped_column(db.String(255), index=True)
    stripe_subscription_id = mapped_column(db.String(255), nullable=True)
    stripe_subscription_cancel_at_period_end = mapped_column(db.Boolean, default=False)
    stripe_subscription_status: Mapped[Optional[StripeSubscriptionStatusEnum]] = mapped_column(
        SQLAlchemyEnum(StripeSubscriptionStatusEnum)
    )
    stripe_subscription_current_period_end = mapped_column(
        db.DateTime(timezone=True), nullable=True
    )
    stripe_subscription_current_period_start = mapped_column(
        db.DateTime(timezone=True), nullable=True
    )

    _PREMIUM_ALIAS_COUNT = 100

    @property
    def password_hash(self) -> str:
        """Return the hashed password."""
        return self._password_hash

    @password_hash.setter
    def password_hash(self, plaintext_password: str) -> None:
        """Hash plaintext password using scrypt and store it."""
        self._password_hash = scrypt.hash(plaintext_password)

    def check_password(self, plaintext_password: str) -> bool:
        """Check the plaintext password against the stored hash."""
        return scrypt.verify(plaintext_password, self._password_hash)

    @property
    def totp_secret(self) -> str | None:
        return decrypt_field(self._totp_secret)

    @totp_secret.setter
    def totp_secret(self, value: str) -> None:
        if value is None:
            self._totp_secret = None
        else:
            self._totp_secret = encrypt_field(value)

    @property
    def email(self) -> str | None:
        return decrypt_field(self._email)

    @email.setter
    def email(self, value: str) -> None:
        self._email = encrypt_field(value)

    @property
    def smtp_server(self) -> str | None:
        return decrypt_field(self._smtp_server)

    @smtp_server.setter
    def smtp_server(self, value: str) -> None:
        self._smtp_server = encrypt_field(value)

    @property
    def smtp_username(self) -> str | None:
        return decrypt_field(self._smtp_username)

    @smtp_username.setter
    def smtp_username(self, value: str) -> None:
        self._smtp_username = encrypt_field(value)

    @property
    def smtp_password(self) -> str | None:
        return decrypt_field(self._smtp_password)

    @smtp_password.setter
    def smtp_password(self, value: str) -> None:
        self._smtp_password = encrypt_field(value)

    @property
    def pgp_key(self) -> str | None:
        return decrypt_field(self._pgp_key)

    @pgp_key.setter
    def pgp_key(self, value: str) -> None:
        if value is None:
            self._pgp_key = None
        else:
            self._pgp_key = encrypt_field(value)

    @property
    def is_free_tier(self) -> bool:
        return self.tier_id is None or self.tier_id == Tier.free_tier_id()

    @property
    def is_business_tier(self) -> bool:
        return self.tier_id == Tier.business_tier_id()

    def set_free_tier(self) -> None:
        self.tier_id = Tier.free_tier_id()

    def set_business_tier(self) -> None:
        self.tier_id = Tier.business_tier_id()

    @property
    def max_aliases(self) -> int:
        alias_mode = current_app.config["ALIAS_MODE"]
        match alias_mode:
            case AliasMode.ALWAYS:
                return 2**32  # just some massive number we'll never have an issue with
            case AliasMode.PREMIUM:
                if self.is_free_tier:
                    return 0
                if not self.is_business_tier:
                    err_msg = f"Programming Error. Unknown tier id: {self.tier_id}"
                    if current_app.config["FLASK_ENV"] == "development":
                        raise Exception(err_msg)
                    current_app.logger.warning(err_msg)
                return self._PREMIUM_ALIAS_COUNT
            case AliasMode.NEVER:
                return 0

        err_msg = f"Programming error. Unhandled alias mode: {alias_mode!r}"
        if current_app.config["FLASK_ENV"] == "development":
            raise Exception(err_msg)
        current_app.logger.warning(err_msg)
        return self._PREMIUM_ALIAS_COUNT

    @property
    def fields_enabled(self) -> bool:
        fields_mode = current_app.config["FIELDS_MODE"]

        if fields_mode == FieldsMode.ALWAYS:
            return True

        return not self.is_free_tier

    def __init__(self, **kwargs: Any) -> None:
        for key in ["password_hash", "_password_hash"]:
            if key in kwargs:
                raise ValueError(f"Key {key!r} cannot be mannually set. Try 'password' instead.")
        pw = kwargs.pop("password", None)
        super().__init__(**kwargs)
        self.password_hash = pw
