# ruff: noqa: F401

from flask import Blueprint

from hushline.settings.admin import register_admin_routes
from hushline.settings.advanced import register_advanced_routes
from hushline.settings.aliases import register_aliases_routes
from hushline.settings.auth import register_auth_routes
from hushline.settings.branding import register_branding_routes
from hushline.settings.delete_account import register_delete_account_routes
from hushline.settings.encryption import register_encryption_routes
from hushline.settings.forms import (
    ChangePasswordForm,
    ChangeUsernameForm,
    DeleteBrandLogoForm,
    DirectoryVisibilityForm,
    DisplayNameForm,
    EmailForwardingForm,
    NewAliasForm,
    PGPKeyForm,
    PGPProtonForm,
    ProfileForm,
    SetHomepageUsernameForm,
    SetMessageStatusTextForm,
    SMTPSettingsForm,
    UpdateBrandAppNameForm,
    UpdateBrandLogoForm,
    UpdateBrandPrimaryColorForm,
    UpdateDirectoryTextForm,
    UpdateProfileHeaderForm,
    UserGuidanceAddPromptForm,
    UserGuidanceEmergencyExitForm,
    UserGuidanceForm,
    UserGuidancePromptContentForm,
)
from hushline.settings.guidance import register_guidance_routes
from hushline.settings.notifications import register_notifications_routes
from hushline.settings.profile import register_profile_routes
from hushline.settings.proton import register_proton_routes
from hushline.settings.replies import register_replies_routes
from hushline.settings.twofa import register_2fa_routes


def create_blueprint() -> Blueprint:
    bp = Blueprint("settings", __file__, url_prefix="/settings")

    register_2fa_routes(bp)
    register_admin_routes(bp)
    register_advanced_routes(bp)
    register_aliases_routes(bp)
    register_auth_routes(bp)
    register_branding_routes(bp)
    register_delete_account_routes(bp)
    register_encryption_routes(bp)
    register_guidance_routes(bp)
    register_notifications_routes(bp)
    register_profile_routes(bp)
    register_proton_routes(bp)
    register_replies_routes(bp)

    return bp
