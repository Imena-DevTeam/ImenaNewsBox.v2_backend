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


class InvitationNotNeeded(Conflict):
    """A member who has already confirmed their email. Admins can always get a new link: it's
    how they reset a forgotten password."""

    code = "invitation_not_needed"
    default_message = (
        "They've already accepted their invitation. Members log in with their family name."
    )


class UserDeactivated(Conflict):
    code = "user_deactivated"
    default_message = "They're deactivated. Reactivate them first."


class EmailAlreadyVerified(Conflict):
    code = "email_already_verified"
    default_message = "Your email address is already confirmed."


class AuthorAlreadyLinked(Conflict):
    code = "author_already_linked"
    default_message = "That Sanity author is already linked to another member."


class NotAWriter(Conflict):
    code = "not_a_writer"
    default_message = "Only social media managers and admins write posts. Change their role first."
