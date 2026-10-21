"""Exceptions raised by this feature."""

from app.core.exceptions import BadRequest, Conflict, NotFound


class UserNotFound(NotFound):
    code = "user_not_found"
    default_message = "That member doesn't exist."


class FamilyNameTaken(Conflict):
    """Also when a deactivated user has it: their family name stays reserved."""

    code = "family_name_taken"
    default_message = "Another member already has this family name."


class EmailTaken(Conflict):
    code = "email_taken"
    default_message = "Another member already uses this email address."


class UnknownSubFamily(BadRequest):
    code = "unknown_sub_family"
    default_message = "That sub-family doesn't exist."


class UnknownPromotion(BadRequest):
    code = "unknown_promotion"
    default_message = "That promotion doesn't exist."


class CannotChangeOwnRole(Conflict):
    """So an admin can't demote themselves and leave nobody able to manage users."""

    code = "cannot_change_own_role"
    default_message = "You can't change your own role. Ask another admin."


class CannotDeactivateSelf(Conflict):
    code = "cannot_deactivate_self"
    default_message = "You can't deactivate yourself. Ask another admin."


class NotAnAdmin(Conflict):
    code = "not_an_admin"
    default_message = "Only admins have a password. Make them an admin first."


class EmailAlreadyVerified(Conflict):
    code = "email_already_verified"
    default_message = "Your email address is already confirmed."
