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
