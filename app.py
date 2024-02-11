# Standard Library Imports
import os
import io
import base64
import logging
import re
import stripe
from logging.handlers import RotatingFileHandler
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Flask Framework and Extensions
from flask import (
    Flask,
    jsonify,
    request,
    render_template,
    redirect,
    url_for,
    session,
    flash,
)
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_wtf import FlaskForm
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash

# Form Handling and Validation
from wtforms import TextAreaField, StringField, PasswordField, IntegerField
from wtforms.validators import DataRequired, Length, Email, ValidationError

# Cryptography and Security
import pyotp
import gnupg
from cryptography.fernet import Fernet

# Database and Error Handling
from sqlalchemy.exc import IntegrityError  # Import IntegrityError

# Environment Variables
from dotenv import load_dotenv

# QR Code Generation
import qrcode

# Utility Decorators
from functools import wraps


# Load environment variables
load_dotenv()

# Retrieve database credentials and secret key from environment
db_user = os.getenv("DB_USER")
db_pass = os.getenv("DB_PASS")
db_name = os.getenv("DB_NAME")
secret_key = os.getenv("SECRET_KEY")
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
stripe_webhook_secret = os.getenv("STRIPE_WH_SECRET")

# Load encryption key
encryption_key = os.getenv("ENCRYPTION_KEY")
if encryption_key is None:
    raise ValueError("Encryption key not found. Please check your .env file.")
fernet = Fernet(encryption_key)


def encrypt_field(data):
    if data is None:
        return None
    return fernet.encrypt(data.encode()).decode()


def decrypt_field(data):
    if data is None:
        return None
    return fernet.decrypt(data.encode()).decode()


app = Flask(__name__)
app.config["SECRET_KEY"] = secret_key
app.config[
    "SQLALCHEMY_DATABASE_URI"
] = f"mysql+pymysql://{db_user}:{db_pass}@localhost/{db_name}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Session configuration for secure cookies
app.config["SESSION_COOKIE_NAME"] = "__Host-session"
app.config["SESSION_COOKIE_SECURE"] = True  # Only send cookies over HTTPS
app.config[
    "SESSION_COOKIE_HTTPONLY"
] = True  # Prevent JavaScript access to session cookie
app.config[
    "SESSION_COOKIE_SAMESITE"
] = "Lax"  # Control cookie sending with cross-site requests


# Initialize GPG with expanded home directory
gpg_home = os.path.expanduser("~/.gnupg")
gpg = gnupg.GPG(gnupghome=gpg_home)

# Initialize extensions
bcrypt = Bcrypt(app)
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Setup file handler
file_handler = RotatingFileHandler(
    "flask.log", maxBytes=1024 * 1024 * 100, backupCount=20
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]"
    )
)

# Add it to the Flask logger
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.DEBUG)


# Password Policy
class ComplexPassword(object):
    def __init__(self, message=None):
        if not message:
            message = "⛔️ Password must include uppercase, lowercase, digit, and a special character."
        self.message = message

    def __call__(self, form, field):
        password = field.data
        if not (
            re.search("[A-Z]", password)
            and re.search("[a-z]", password)
            and re.search("[0-9]", password)
            and re.search("[^A-Za-z0-9]", password)
        ):
            raise ValidationError(self.message)


# Database Models
class User(db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    primary_username = db.Column(db.String(80), unique=True, nullable=False)
    display_name = db.Column(db.String(80))
    _password_hash = db.Column("password_hash", db.String(255))
    _totp_secret = db.Column("totp_secret", db.String(255))
    _email = db.Column("email", db.String(255))
    _smtp_server = db.Column("smtp_server", db.String(255))
    smtp_port = db.Column(db.Integer)
    _smtp_username = db.Column("smtp_username", db.String(255))
    _smtp_password = db.Column("smtp_password", db.String(255))
    _pgp_key = db.Column("pgp_key", db.Text)
    is_verified = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    has_paid = db.Column(db.Boolean, default=False)
    stripe_customer_id = db.Column(db.String(255), unique=True, nullable=True)
    stripe_subscription_id = db.Column(db.String(255), unique=True, nullable=True)
    # Corrected the relationship and backref here
    secondary_users = db.relationship(
        "SecondaryUser", backref=db.backref("primary_user", lazy=True)
    )

    @property
    def password_hash(self):
        return decrypt_field(self._password_hash)

    @password_hash.setter
    def password_hash(self, value):
        self._password_hash = encrypt_field(value)

    @property
    def totp_secret(self):
        return decrypt_field(self._totp_secret)

    @totp_secret.setter
    def totp_secret(self, value):
        if value is None:
            self._totp_secret = None
        else:
            self._totp_secret = encrypt_field(value)

    @property
    def email(self):
        return decrypt_field(self._email)

    @email.setter
    def email(self, value):
        self._email = encrypt_field(value)

    @property
    def smtp_server(self):
        return decrypt_field(self._smtp_server)

    @smtp_server.setter
    def smtp_server(self, value):
        self._smtp_server = encrypt_field(value)

    @property
    def smtp_username(self):
        return decrypt_field(self._smtp_username)

    @smtp_username.setter
    def smtp_username(self, value):
        self._smtp_username = encrypt_field(value)

    @property
    def smtp_password(self):
        return decrypt_field(self._smtp_password)

    @smtp_password.setter
    def smtp_password(self, value):
        self._smtp_password = encrypt_field(value)

    @property
    def pgp_key(self):
        return decrypt_field(self._pgp_key)

    @pgp_key.setter
    def pgp_key(self, value):
        if value is None:
            self._pgp_key = None
        else:
            self._pgp_key = encrypt_field(value)

    def update_display_name(self, new_display_name):
        """Update the user's display name and remove verification status if the user is verified."""
        self.display_name = new_display_name
        if self.is_verified:
            self.is_verified = False

    # In the User model
    def update_username(self, new_username):
        """Update the user's username and remove verification status if the user is verified."""
        app.logger.debug(f"Attempting to update username to {new_username}")
        self.username = new_username
        if self.is_verified:
            self.is_verified = False
            app.logger.debug(f"Username updated, Verification status set to False")


class SecondaryUser(db.Model):
    __tablename__ = "secondary_user"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    # This foreign key points to the 'user' table's 'id' field
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    display_name = db.Column(db.String(80), nullable=True)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    _content = db.Column(
        "content", db.Text, nullable=False
    )  # Encrypted content stored here
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # Add a foreign key to reference secondary usernames
    secondary_user_id = db.Column(
        db.Integer, db.ForeignKey("secondary_user.id"), nullable=True
    )

    # Relationship with User model
    user = db.relationship("User", backref=db.backref("messages", lazy=True))

    # New relationship to link a message to a specific secondary username (if applicable)
    secondary_user = db.relationship("SecondaryUser", backref="messages")

    @property
    def content(self):
        """Decrypt and return the message content."""
        return decrypt_field(self._content)

    @content.setter
    def content(self, value):
        """Encrypt and store the message content."""
        self._content = encrypt_field(value)


class InviteCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(255), unique=True, nullable=False)
    expiration_date = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False, nullable=False)

    def __repr__(self):
        return "<InviteCode %r>" % self.code


class MessageForm(FlaskForm):
    content = TextAreaField(
        "Message",
        validators=[DataRequired(), Length(max=2000)],
        render_kw={"placeholder": "Include a contact method if you want a response..."},
    )


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])


class TwoFactorForm(FlaskForm):
    verification_code = StringField(
        "2FA Code", validators=[DataRequired(), Length(min=6, max=6)]
    )


class RegistrationForm(FlaskForm):
    username = StringField(
        "Username", validators=[DataRequired(), Length(min=4, max=25)]
    )
    password = PasswordField(
        "Password",
        validators=[
            DataRequired(),
            Length(min=18, max=128),
            ComplexPassword(),
        ],
    )
    invite_code = StringField(
        "Invite Code", validators=[DataRequired(), Length(min=6, max=25)]
    )


class ChangePasswordForm(FlaskForm):
    old_password = PasswordField("Old Password", validators=[DataRequired()])
    new_password = PasswordField(
        "New Password",
        validators=[
            DataRequired(),
            Length(min=18, max=128),
            ComplexPassword(),
        ],
    )


class ChangeUsernameForm(FlaskForm):
    new_username = StringField(
        "New Username", validators=[DataRequired(), Length(min=4, max=25)]
    )


class SMTPSettingsForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    smtp_server = StringField("SMTP Server", validators=[DataRequired()])
    smtp_port = IntegerField("SMTP Port", validators=[DataRequired()])
    smtp_username = StringField("SMTP Username", validators=[DataRequired()])
    smtp_password = PasswordField("SMTP Password", validators=[DataRequired()])


class PGPKeyForm(FlaskForm):
    pgp_key = TextAreaField("PGP Key", validators=[Length(max=20000)])


class DisplayNameForm(FlaskForm):
    display_name = StringField("Display Name", validators=[Length(max=100)])


def require_2fa(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session or not session.get("is_authenticated", False):
            flash("👉 Please complete authentication.")
            return redirect(url_for("login"))
        if session.get("2fa_required", False) and not session.get(
            "2fa_verified", False
        ):
            flash("👉 2FA verification required.")
            return redirect(url_for("verify_2fa_login"))
        return f(*args, **kwargs)

    return decorated_function


# Error Handler
@app.errorhandler(Exception)
def handle_exception(e):
    # Log the error and stacktrace
    app.logger.error(f"Error: {e}", exc_info=True)
    return "An internal server error occurred", 500


# Routes
@app.route("/")
def index():
    if "user_id" in session:
        user = User.query.get(session["user_id"])
        if user:
            return redirect(url_for("inbox", username=user.primary_username))
        else:
            # Handle case where user ID in session does not exist in the database
            flash("🫥 User not found. Please log in again.")
            session.pop("user_id", None)  # Clear the invalid user_id from session
            return redirect(url_for("login"))
    else:
        return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    form = RegistrationForm()

    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        invite_code_input = form.invite_code.data

        # Validate the invite code
        invite_code = InviteCode.query.filter_by(
            code=invite_code_input, used=False
        ).first()
        if not invite_code or invite_code.expiration_date < datetime.utcnow():
            flash("⛔️ Invalid or expired invite code.", "error")
            return redirect(url_for("register"))

        # Check for existing primary_username instead of username
        if User.query.filter_by(primary_username=username).first():
            flash("💔 Username already taken.", "error")
            return redirect(url_for("register"))

        # Hash the password and create the user
        password_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        new_user = User(primary_username=username, password_hash=password_hash)

        # Add user and mark invite code as used
        db.session.add(new_user)
        invite_code.used = True
        db.session.commit()

        flash("👍 Registration successful! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", form=form)


@app.route("/enable-2fa", methods=["GET", "POST"])
def enable_2fa():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))

    user = User.query.get(user_id)
    form = TwoFactorForm()

    if form.validate_on_submit():
        verification_code = form.verification_code.data
        temp_totp_secret = session.get("temp_totp_secret")
        if temp_totp_secret and pyotp.TOTP(temp_totp_secret).verify(verification_code):
            user.totp_secret = temp_totp_secret
            db.session.commit()
            session.pop("temp_totp_secret", None)
            flash("👍 2FA setup successful. Please log in again with 2FA.")
            return redirect(url_for("logout"))  # Redirect to logout
        else:
            flash("⛔️ Invalid 2FA code. Please try again.")
            return redirect(url_for("enable_2fa"))

    # Generate new 2FA secret and QR code
    temp_totp_secret = pyotp.random_base32()
    session["temp_totp_secret"] = temp_totp_secret
    session["is_setting_up_2fa"] = True
    totp_uri = pyotp.totp.TOTP(temp_totp_secret).provisioning_uri(
        name=user.primary_username, issuer_name="HushLine"
    )
    img = qrcode.make(totp_uri)
    buffered = io.BytesIO()
    img.save(buffered)
    qr_code_img = (
        "data:image/png;base64," + base64.b64encode(buffered.getvalue()).decode()
    )

    # Pass the text-based pairing code to the template
    return render_template(
        "enable_2fa.html",
        form=form,
        qr_code_img=qr_code_img,
        text_code=temp_totp_secret,
    )


@app.route("/disable-2fa", methods=["POST"])
def disable_2fa():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))

    user = db.session.get(User, user_id)
    user.totp_secret = None
    db.session.commit()
    flash("🔓 2FA has been disabled.")
    return redirect(url_for("settings"))


@app.route("/confirm-disable-2fa", methods=["GET"])
def confirm_disable_2fa():
    return render_template("confirm_disable_2fa.html")


@app.route("/show-qr-code")
def show_qr_code():
    user = User.query.get(session["user_id"])
    if not user or not user.totp_secret:
        return redirect(url_for("enable_2fa"))

    form = TwoFactorForm()

    totp_uri = pyotp.totp.TOTP(user.totp_secret).provisioning_uri(
        name=user.primary_username, issuer_name="Hush Line"
    )
    img = qrcode.make(totp_uri)

    # Convert QR code to a data URI
    buffered = io.BytesIO()
    img.save(buffered)
    img_str = base64.b64encode(buffered.getvalue()).decode()
    qr_code_img = f"data:image/png;base64,{img_str}"

    return render_template(
        "show_qr_code.html",
        form=form,
        qr_code_img=qr_code_img,
        user_secret=user.totp_secret,
    )


@app.route("/verify-2fa-setup", methods=["POST"])
def verify_2fa_setup():
    user = User.query.get(session["user_id"])
    if not user:
        return redirect(url_for("login"))

    verification_code = request.form["verification_code"]
    totp = pyotp.TOTP(user.totp_secret)
    if totp.verify(verification_code):
        flash("👍 2FA setup successful. Please log in again.")
        session.pop("is_setting_up_2fa", None)
        return redirect(url_for("logout"))
    else:
        flash("⛔️ Invalid 2FA code. Please try again.")
        return redirect(url_for("show_qr_code"))


@app.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data  # This is input from the form
        password = form.password.data

        # Corrected to use primary_username for filter_by
        user = User.query.filter_by(primary_username=username).first()

        if user and bcrypt.check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            session[
                "username"
            ] = (
                user.primary_username
            )  # Use primary_username here if you need to store username in session
            session["is_authenticated"] = True  # User is authenticated
            session["2fa_required"] = user.totp_secret is not None
            session["2fa_verified"] = False
            session["is_admin"] = user.is_admin

            if user.totp_secret:
                return redirect(url_for("verify_2fa_login"))
            else:
                session["2fa_verified"] = True  # Direct login if 2FA not enabled
                return redirect(
                    url_for("inbox", username=user.primary_username)
                )  # Use primary_username
        else:
            flash("Invalid username or password")

    return render_template("login.html", form=form)


@app.route("/admin")
@require_2fa
def admin():
    if "user_id" not in session or not session.get("is_admin", False):
        flash("⛔️ Unauthorized access.")
        return redirect(url_for("index"))

    user = User.query.get(session["user_id"])
    if not user:
        flash("🫥 User not found.")
        return redirect(url_for("login"))

    user_count = User.query.count()  # Total number of users
    two_fa_count = User.query.filter(
        User._totp_secret != None
    ).count()  # Users with 2FA
    pgp_key_count = (
        User.query.filter(User._pgp_key != None).filter(User._pgp_key != "").count()
    )  # Users with PGP key

    # Calculate percentages
    two_fa_percentage = (two_fa_count / user_count * 100) if user_count else 0
    pgp_key_percentage = (pgp_key_count / user_count * 100) if user_count else 0

    return render_template(
        "admin.html",
        user=user,
        user_count=user_count,
        two_fa_count=two_fa_count,
        pgp_key_count=pgp_key_count,
        two_fa_percentage=two_fa_percentage,
        pgp_key_percentage=pgp_key_percentage,
    )


@app.route("/verify-2fa-login", methods=["GET", "POST"])
def verify_2fa_login():
    # Redirect to login if user is not authenticated
    if "user_id" not in session or not session.get("2fa_required", False):
        return redirect(url_for("login"))

    user = User.query.get(session["user_id"])
    if not user:
        flash("🫥 User not found. Please login again.")
        session.clear()  # Clearing the session for security
        return redirect(url_for("login"))

    form = TwoFactorForm()

    if form.validate_on_submit():
        verification_code = form.verification_code.data
        totp = pyotp.TOTP(user.totp_secret)
        if totp.verify(verification_code):
            session["2fa_verified"] = True  # Set 2FA verification flag
            return redirect(url_for("inbox", username=user.primary_username))
        else:
            flash("⛔️ Invalid 2FA code. Please try again.")

    return render_template("verify_2fa_login.html", form=form)


@app.route("/inbox/<username>")
@require_2fa
def inbox(username):
    # Redirect if not logged in
    if "user_id" not in session:
        flash("Please log in to access your inbox.")
        return redirect(url_for("login"))

    # Initialize variables
    primary_user = None
    secondary_user = None
    messages = []

    # Try to find a primary user with the given username
    user = User.query.filter_by(primary_username=username).first()
    if user:
        primary_user = user
        messages = (
            Message.query.filter_by(user_id=user.id).order_by(Message.id.desc()).all()
        )
    else:
        # If not found, try to find a secondary user and its related messages
        secondary_user = SecondaryUser.query.filter_by(username=username).first()
        if secondary_user:
            messages = (
                Message.query.filter_by(secondary_user_id=secondary_user.id)
                .order_by(Message.id.desc())
                .all()
            )
            # We use the primary user related to the secondary user for some operations
            user = secondary_user.primary_user

    if not user:
        flash("User not found. Please log in again.")
        return redirect(url_for("login"))

    return render_template(
        "inbox.html",
        user=user,
        secondary_user=secondary_user,
        messages=messages,
        is_secondary=bool(secondary_user),
    )


@app.route("/settings", methods=["GET", "POST"])
@require_2fa
def settings():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))

    user = User.query.get(user_id)
    if not user:
        flash("🫥 User not found.")
        return redirect(url_for("login"))

    # Fetch all secondary usernames for the current user
    secondary_usernames = SecondaryUser.query.filter_by(user_id=user.id).all()

    # Initialize forms
    change_password_form = ChangePasswordForm()
    change_username_form = ChangeUsernameForm()
    smtp_settings_form = SMTPSettingsForm()
    pgp_key_form = PGPKeyForm()
    display_name_form = DisplayNameForm()

    # Handle form submissions
    if request.method == "POST":
        # Handle Display Name Form Submission
        if (
            "update_display_name" in request.form
            and display_name_form.validate_on_submit()
        ):
            user.update_display_name(display_name_form.display_name.data.strip())
            db.session.commit()
            flash("👍 Display name updated successfully.")
            app.logger.debug(
                f"Display name updated to {user.display_name}, Verification status: {user.is_verified}"
            )
            return redirect(url_for("settings"))

        # Handle Change Username Form Submission
        elif (
            "change_username" in request.form
            and change_username_form.validate_on_submit()
        ):
            new_username = change_username_form.new_username.data
            existing_user = User.query.filter_by(primary_username=new_username).first()
            if existing_user:
                flash("💔 This username is already taken.")
            else:
                user.update_username(new_username)
                db.session.commit()
                session["username"] = new_username  # Update username in session
                flash("👍 Username changed successfully.")
                app.logger.debug(
                    f"Username updated to {user.primary_username}, Verification status: {user.is_verified}"
                )
            return redirect(url_for("settings"))

        # Handle SMTP Settings Form Submission
        elif smtp_settings_form.validate_on_submit():
            user.email = smtp_settings_form.email.data
            user.smtp_server = smtp_settings_form.smtp_server.data
            user.smtp_port = smtp_settings_form.smtp_port.data
            user.smtp_username = smtp_settings_form.smtp_username.data
            user.smtp_password = smtp_settings_form.smtp_password.data
            db.session.commit()
            flash("👍 SMTP settings updated successfully.")
            return redirect(url_for("settings"))

        # Handle PGP Key Form Submission
        elif pgp_key_form.validate_on_submit():
            user.pgp_key = pgp_key_form.pgp_key.data
            db.session.commit()
            flash("👍 PGP key updated successfully.")
            return redirect(url_for("settings"))

        # Handle Change Password Form Submission
        elif change_password_form.validate_on_submit():
            if bcrypt.check_password_hash(
                user.password_hash, change_password_form.old_password.data
            ):
                user.password_hash = bcrypt.generate_password_hash(
                    change_password_form.new_password.data
                ).decode("utf-8")
                db.session.commit()
                flash("👍 Password changed successfully.")
            else:
                flash("⛔️ Incorrect old password.")
            return redirect(url_for("settings"))

    # Prepopulate form fields
    smtp_settings_form.email.data = user.email
    smtp_settings_form.smtp_server.data = user.smtp_server
    smtp_settings_form.smtp_port.data = user.smtp_port
    smtp_settings_form.smtp_username.data = user.smtp_username
    pgp_key_form.pgp_key.data = user.pgp_key
    display_name_form.display_name.data = user.display_name or user.primary_username

    return render_template(
        "settings.html",
        user=user,
        secondary_usernames=secondary_usernames,
        smtp_settings_form=smtp_settings_form,
        change_password_form=change_password_form,
        change_username_form=change_username_form,
        pgp_key_form=pgp_key_form,
        display_name_form=display_name_form,
    )


@app.route("/toggle-2fa", methods=["POST"])
def toggle_2fa():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))

    user = db.session.get(User, user_id)
    if user.totp_secret:
        return redirect(url_for("disable_2fa"))
    else:
        return redirect(url_for("enable_2fa"))


@app.route("/change-password", methods=["POST"])
def change_password():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))

    # Retrieve the user using the user_id
    user = User.query.get(user_id)
    change_password_form = ChangePasswordForm(request.form)
    change_username_form = ChangeUsernameForm()
    smtp_settings_form = SMTPSettingsForm()
    pgp_key_form = PGPKeyForm()
    display_name_form = DisplayNameForm()

    if change_password_form.validate_on_submit():
        old_password = change_password_form.old_password.data
        new_password = change_password_form.new_password.data

        if bcrypt.check_password_hash(user.password_hash, old_password):
            user.password_hash = bcrypt.generate_password_hash(new_password).decode(
                "utf-8"
            )
            db.session.commit()
            flash("👍 Password successfully changed.")
            return redirect(url_for("settings"))
        else:
            flash("⛔️ Incorrect old password.")

    return render_template(
        "settings.html",
        change_password_form=change_password_form,
        change_username_form=change_username_form,
        smtp_settings_form=smtp_settings_form,
        pgp_key_form=pgp_key_form,
        display_name_form=display_name_form,
        user=user,
    )


@app.route("/change-username", methods=["POST"])
@require_2fa
def change_username():
    user_id = session.get("user_id")
    if not user_id:
        flash("Please log in to continue.", "info")
        return redirect(url_for("login"))

    # Retrieve the form data for the new username
    new_username = request.form.get("new_username")
    if not new_username:
        flash("No new username provided.", "error")
        return redirect(url_for("settings"))

    # Retrieve the current user
    user = User.query.get(user_id)
    if user.primary_username == new_username:
        flash("New username is the same as the current username.", "info")
        return redirect(url_for("settings"))

    # Check if the new username is already taken
    existing_user = User.query.filter_by(primary_username=new_username).first()
    if existing_user:
        flash("This username is already taken.", "error")
        return redirect(url_for("settings"))

    # At this point, the new username is available, so update the user's username
    user.primary_username = new_username
    db.session.commit()
    session[
        "primary_username"
    ] = new_username  # Update the session with the new username

    flash("Username successfully changed.", "success")
    return redirect(url_for("settings"))


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("2fa_verified", None)  # Clear 2FA verification flag
    return redirect(url_for("index"))


def get_email_from_pgp_key(pgp_key):
    try:
        # Import the PGP key
        imported_key = gpg.import_keys(pgp_key)

        if imported_key.count > 0:
            # Get the Key ID of the imported key
            key_id = imported_key.results[0]["fingerprint"][-16:]

            # List all keys to find the matching key
            all_keys = gpg.list_keys()
            for key in all_keys:
                if key["keyid"] == key_id:
                    # Extract email from the uid (user ID)
                    uids = key["uids"][0]
                    email_start = uids.find("<") + 1
                    email_end = uids.find(">")
                    if email_start > 0 and email_end > email_start:
                        return uids[email_start:email_end]
    except Exception as e:
        app.logger.error(f"Error extracting email from PGP key: {e}")

    return None


@app.route("/submit_message/<username>", methods=["GET", "POST"])
def submit_message(username):
    form = MessageForm()

    user = None
    secondary_user = None
    display_name_or_username = ""

    # Attempt to find a primary user first
    primary_user = User.query.filter_by(primary_username=username).first()

    if primary_user:
        user = primary_user
        display_name_or_username = (
            primary_user.display_name or primary_user.primary_username
        )
    else:
        # If not a primary user, attempt to find a secondary user
        secondary_user = SecondaryUser.query.filter_by(username=username).first()
        if secondary_user:
            user = (
                secondary_user.primary_user
            )  # Associate to the primary user of the secondary
            display_name_or_username = (
                secondary_user.display_name or secondary_user.username
            )

            # Redirect if the secondary username is used and the primary user hasn't paid
            if not user.has_paid:
                flash(
                    "⚠️ This feature requires a premium account. Please upgrade to access.",
                    "warning",
                )
                return redirect(url_for("settings"))

    if not user:
        flash("🫥 User not found.")
        return redirect(url_for("index"))

    if form.validate_on_submit():
        content = form.content.data
        email_content = content  # Assume non-encrypted content initially
        email_sent = False  # To track if email was sent

        # Encrypt message if the target user has a PGP key
        if user.pgp_key:
            pgp_email = get_email_from_pgp_key(user.pgp_key)
            if pgp_email:
                encrypted_content = encrypt_message(content, pgp_email)
                if encrypted_content:
                    email_content = encrypted_content  # Use encrypted content for email
                else:
                    flash("⛔️ Failed to encrypt message with PGP key.")
                    return redirect(url_for("submit_message", username=username))
            else:
                flash("⛔️ Unable to extract email from PGP key.")
                return redirect(url_for("submit_message", username=username))

        # Determine whether to attribute message to a primary or secondary user
        if primary_user:
            new_message = Message(content=email_content, user_id=user.id)
        elif secondary_user:
            new_message = Message(
                content=email_content,
                user_id=user.id,
                secondary_user_id=secondary_user.id,
            )
        db.session.add(new_message)
        db.session.commit()

        # Send email notification if SMTP settings are configured
        if all(
            [
                user.email,
                user.smtp_server,
                user.smtp_port,
                user.smtp_username,
                user.smtp_password,
            ]
        ):
            email_sent = send_email(user.email, "New Message", email_content, user)

        flash("👍 Message submitted" + (" and emailed" if email_sent else ""))
        return redirect(url_for("submit_message", username=username))

    return render_template(
        "submit_message.html",
        form=form,
        user=user,
        secondary_user=secondary_user,  # Pass secondary user if applicable
        username=username,
        display_name_or_username=display_name_or_username,
        current_user_id=session.get("user_id"),
    )


def send_email(recipient, subject, body, user):
    app.logger.debug(
        f"SMTP settings being used: Server: {user.smtp_server}, Port: {user.smtp_port}, Username: {user.smtp_username}"
    )
    msg = MIMEMultipart()
    msg["From"] = user.email
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        app.logger.debug("Attempting to connect to SMTP server")
        with smtplib.SMTP(user.smtp_server, user.smtp_port) as server:
            app.logger.debug("Starting TLS")
            server.starttls()

            app.logger.debug("Attempting to log in to SMTP server")
            server.login(user.smtp_username, user.smtp_password)

            app.logger.debug("Sending email")
            text = msg.as_string()
            server.sendmail(user.email, recipient, text)
            app.logger.info("Email sent successfully.")
            return True
    except Exception as e:
        app.logger.error(f"Error sending email: {e}", exc_info=True)
        return False


def is_valid_pgp_key(key):
    app.logger.debug(f"Attempting to import key: {key}")
    try:
        imported_key = gpg.import_keys(key)
        app.logger.info(f"Key import attempt: {imported_key.results}")
        return imported_key.count > 0
    except Exception as e:
        app.logger.error(f"Error importing PGP key: {e}")
        return False


@app.route("/update_pgp_key", methods=["GET", "POST"])
def update_pgp_key():
    user_id = session.get("user_id")
    if not user_id:
        flash("⛔️ User not authenticated.")
        return redirect(url_for("login"))

    user = db.session.get(User, user_id)
    form = PGPKeyForm()
    if form.validate_on_submit():
        pgp_key = form.pgp_key.data

        if pgp_key.strip() == "":
            # If the field is empty, remove the PGP key
            user.pgp_key = None
        elif is_valid_pgp_key(pgp_key):
            # If the field is not empty and the key is valid, update the PGP key
            user.pgp_key = pgp_key
        else:
            # If the PGP key is invalid
            flash("⛔️ Invalid PGP key format or import failed.")
            return redirect(url_for("settings"))

        db.session.commit()
        flash("👍 PGP key updated successfully.")
        return redirect(url_for("settings"))
    return render_template("settings.html", form=form)


def encrypt_message(message, recipient_email):
    gpg = gnupg.GPG(gnupghome=gpg_home, options=["--trust-model", "always"])
    app.logger.info(f"Encrypting message for recipient: {recipient_email}")

    try:
        # Ensure the message is a byte string encoded in UTF-8
        if isinstance(message, str):
            message = message.encode("utf-8")
        encrypted_data = gpg.encrypt(
            message, recipients=recipient_email, always_trust=True
        )

        if not encrypted_data.ok:
            app.logger.error(f"Encryption failed: {encrypted_data.status}")
            return None

        return str(encrypted_data)
    except Exception as e:
        app.logger.error(f"Error during encryption: {e}")
        return None


def list_keys():
    try:
        public_keys = gpg.list_keys()
        app.logger.info("Public keys in the keyring:")
        for key in public_keys:
            app.logger.info(f"Key: {key}")
    except Exception as e:
        app.logger.error(f"Error listing keys: {e}")


# Call this function after key import or during troubleshooting
list_keys()


@app.route("/update_smtp_settings", methods=["GET", "POST"])
def update_smtp_settings():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))

    user = db.session.get(User, user_id)
    if not user:
        flash("⛔️ User not found")
        return redirect(url_for("settings"))

    # Initialize forms
    change_password_form = ChangePasswordForm()
    change_username_form = ChangeUsernameForm()
    smtp_settings_form = SMTPSettingsForm()
    pgp_key_form = PGPKeyForm()

    # Handling SMTP settings form submission
    if smtp_settings_form.validate_on_submit():
        # Updating SMTP settings from form data
        user.email = smtp_settings_form.email.data
        user.smtp_server = smtp_settings_form.smtp_server.data
        user.smtp_port = smtp_settings_form.smtp_port.data
        user.smtp_username = smtp_settings_form.smtp_username.data
        user.smtp_password = smtp_settings_form.smtp_password.data

        db.session.commit()
        flash("👍 SMTP settings updated successfully")
        return redirect(url_for("settings"))

    # Prepopulate SMTP settings form fields
    smtp_settings_form.email.data = user.email
    smtp_settings_form.smtp_server.data = user.smtp_server
    smtp_settings_form.smtp_port.data = user.smtp_port
    smtp_settings_form.smtp_username.data = user.smtp_username
    # Note: Password fields are typically not prepopulated for security reasons

    pgp_key_form.pgp_key.data = user.pgp_key

    return render_template(
        "settings.html",
        user=user,
        smtp_settings_form=smtp_settings_form,
        change_password_form=change_password_form,
        change_username_form=change_username_form,
        pgp_key_form=pgp_key_form,
    )


@app.route("/delete_message/<int:message_id>", methods=["POST"])
def delete_message(message_id):
    if "user_id" not in session:
        flash("🔑 Please log in to continue.")
        return redirect(url_for("login"))

    user = User.query.get(session["user_id"])
    if not user:
        flash("🫥 User not found. Please log in again.")
        return redirect(url_for("login"))

    message = Message.query.get(message_id)
    if message and message.user_id == user.id:
        db.session.delete(message)
        db.session.commit()
        flash("🗑️ Message deleted successfully.")
    else:
        flash("⛔️ Message not found or unauthorized access.")

    return redirect(url_for("inbox", username=user.primary_username))


@app.route("/delete-account", methods=["POST"])
@require_2fa
def delete_account():
    user_id = session.get("user_id")
    if not user_id:
        flash("🔑 Please log in to continue.")
        return redirect(url_for("login"))

    user = User.query.get(user_id)
    if user:
        # Delete user and related records
        db.session.delete(user)
        db.session.commit()

        # Clear session and log out
        session.clear()
        flash("👋 Your account has been deleted.")
        return redirect(url_for("index"))
    else:
        flash("🫥 User not found. Please log in again.")
        return redirect(url_for("login"))


@app.route("/add-secondary-username", methods=["POST"])
@require_2fa
def add_secondary_username():
    user_id = session.get("user_id")
    if not user_id:
        flash("👉 Please log in to continue.", "warning")
        return redirect(url_for("login"))

    user = User.query.get(user_id)
    if not user:
        flash("🫥 User not found.", "error")
        return redirect(url_for("logout"))

    # Check if the user has paid for the premium feature
    if not user.has_paid:
        flash(
            "⚠️ This feature requires a paid account.",
            "warning",
        )
        return redirect(url_for("create_checkout_session"))

    # Check if the user already has the maximum number of secondary usernames
    if len(user.secondary_users) >= 5:
        flash("⚠️ You have reached the maximum number of secondary usernames.", "error")
        return redirect(url_for("settings"))

    username = request.form.get("username").strip()
    if not username:
        flash("⛔️ Username is required.", "error")
        return redirect(url_for("settings"))

    # Check if the secondary username is already taken
    existing_user = SecondaryUser.query.filter_by(username=username).first()
    if existing_user:
        flash("⚠️ This username is already taken.", "error")
        return redirect(url_for("settings"))

    # Add the new secondary username
    new_secondary_user = SecondaryUser(username=username, user_id=user.id)
    db.session.add(new_secondary_user)
    db.session.commit()
    flash("👍 Username added successfully.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/secondary/<secondary_username>/update", methods=["POST"])
@require_2fa
def update_secondary_username(secondary_username):
    # Ensure the user is logged in
    user_id = session.get("user_id")
    if not user_id:
        flash("👉 Please log in to continue.", "warning")
        return redirect(url_for("login"))

    # Find the secondary user in the database
    secondary_user = SecondaryUser.query.filter_by(
        username=secondary_username, user_id=user_id
    ).first_or_404()

    # Update the secondary user's display name from the form data
    new_display_name = request.form.get("display_name").strip()
    if new_display_name:
        secondary_user.display_name = new_display_name
        db.session.commit()
        flash("Display name updated successfully.", "success")
    else:
        flash("Display name cannot be empty.", "error")

    # Redirect back to the settings page for the secondary user
    return redirect(
        url_for("secondary_user_settings", secondary_username=secondary_username)
    )


@app.route("/settings/secondary/<secondary_username>", methods=["GET", "POST"])
@require_2fa
def secondary_user_settings(secondary_username):
    secondary_user = SecondaryUser.query.filter_by(
        username=secondary_username
    ).first_or_404()
    if request.method == "POST":
        # Update the secondary user's display name or other settings
        secondary_user.display_name = request.form.get("display_name", "").strip()
        db.session.commit()
        flash("👍 Settings updated successfully.")
        return redirect(
            url_for("secondary_user_settings", secondary_username=secondary_username)
        )

    return render_template(
        "secondary_user_settings.html", secondary_user=secondary_user
    )


@app.route("/create-checkout-session", methods=["POST"])
def create_checkout_session():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "User not authenticated"}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    try:
        # Create or update Stripe Customer and store the Stripe Customer ID
        if not user.stripe_customer_id:
            customer = stripe.Customer.create(email=user.email)
            user.stripe_customer_id = customer.id
            db.session.commit()
        else:
            customer = stripe.Customer.retrieve(user.stripe_customer_id)

        # Store the origin page in the session
        origin_page = request.referrer or url_for("index")
        session["origin_page"] = origin_page

        price_id = "price_1OhiU5LcBPqjxU07a4eKQHrO"  # Test
        # price_id = "price_1OhhYFLcBPqjxU07u2wYbUcF"

        checkout_session = stripe.checkout.Session.create(
            customer=user.stripe_customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=url_for("payment_success", _external=True)
            + f"?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=url_for("payment_cancel", _external=True)
            + f"?session_id={{CHECKOUT_SESSION_ID}}",
        )
        return jsonify({"id": checkout_session.id})
    except Exception as e:
        current_app.logger.error(f"Failed to create checkout session: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/payment-success")
def payment_success():
    # Retrieve the origin page from the query string
    origin_page = request.args.get("origin", url_for("index"))
    # Retrieve the session ID from the query string (or through another method provided by Stripe)
    session_id = request.args.get("session_id")

    if "user_id" in session:
        user_id = session["user_id"]
        user = User.query.get(user_id)
        if user:
            # Retrieve the checkout session to get the subscription ID
            checkout_session = stripe.checkout.Session.retrieve(session_id)
            subscription_id = (
                checkout_session.subscription
            )  # This is where you get the subscription ID

            # Now set the has_paid field and the subscription ID
            user.has_paid = True
            user.stripe_subscription_id = subscription_id  # Save the subscription ID
            db.session.commit()

            flash(
                "🎉 Payment successful! Your account has been upgraded.",
                "success",
            )
        else:
            flash("🫥 User not found.", "error")
    else:
        flash("⛔️ You are not logged in.", "warning")

    return redirect(origin_page)


@app.route("/payment-cancel")
def payment_cancel():
    # Retrieve the origin page from the query string
    origin_page = request.args.get("origin", url_for("index"))
    flash("👍 Payment was cancelled.", "warning")
    return redirect(origin_page)


@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, stripe_webhook_secret
        )

        # Handle the checkout.session.completed event
        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            # Find user by session's customer ID and update has_paid
            user = find_user_by_stripe_customer_id(session["customer"])
            if user:
                user.has_paid = True
                db.session.commit()

        # Handle the customer.subscription.deleted event
        elif event["type"] == "customer.subscription.deleted":
            subscription = event["data"]["object"]
            user = find_user_by_stripe_customer_id(subscription["customer"])
            if user:
                user.has_paid = False
                db.session.commit()

        # Handle the invoice.payment_failed event
        elif event["type"] == "invoice.payment_failed":
            invoice = event["data"]["object"]
            user = find_user_by_stripe_customer_id(invoice["customer"])
            if user:
                user.has_paid = False
                db.session.commit()

        return jsonify({"status": "success"})
    except ValueError as e:
        # Invalid payload
        return jsonify({"error": "Invalid payload"}), 400
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        return jsonify({"error": "Invalid signature"}), 400
    except Exception as e:
        # Other exceptions
        return jsonify({"error": str(e)}), 400


def find_user_by_stripe_customer_id(customer_id):
    return User.query.filter_by(stripe_customer_id=customer_id).first()


@app.route("/cancel-subscription", methods=["POST"])
@require_2fa  # Assuming you're using 2FA requirement
def cancel_subscription():
    user_id = session.get("user_id")
    if not user_id:
        flash("Please log in to continue.", "warning")
        return redirect(url_for("login"))

    user = User.query.get(user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("logout"))

    try:
        # Assuming you have a field on the User model for the Stripe subscription ID
        subscription_id = user.stripe_subscription_id
        if not subscription_id:
            flash("No subscription found.", "error")
            return redirect(url_for("settings"))

        # Cancel the subscription using the Stripe API
        stripe.Subscription.delete(subscription_id)

        # Update user's subscription status in the database
        user.has_paid = False
        user.stripe_subscription_id = None  # Clear the subscription ID
        db.session.commit()

        flash("Your subscription has been canceled.", "success")
    except Exception as e:
        app.logger.error(f"Failed to cancel subscription: {e}")
        flash("An error occurred while canceling your subscription.", "error")

    return redirect(url_for("settings"))


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run()
