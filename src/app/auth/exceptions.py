"""Exceptions raised by this feature."""

from app.core.exceptions import Forbidden, TooManyRequests, Unauthorized


class NotAuthenticated(Unauthorized):
    code = "not_authenticated"
    default_message = "Log in to continue."


class SessionEnded(Unauthorized):
    """The token is genuine, but its session was revoked or has expired, or the user was
    deactivated. Deliberately one error for all three, so the reason isn't revealed."""

    code = "session_ended"
    default_message = "Your session has ended. Log in again."


class InsufficientRole(Forbidden):
    code = "insufficient_role"
    default_message = "Your role doesn't allow this."


class NotOwner(Forbidden):
    code = "not_owner"
    default_message = "You can only change your own things."


class OriginNotAllowed(Forbidden):
    """A cookie-authenticated request (refresh or logout) came from a page that isn't ours."""

    code = "origin_not_allowed"
    default_message = "This request must come from the Imena NewsBox website."


class InvalidCode(Unauthorized):
    """Wrong, used, expired or out of attempts: one error, so a guesser learns nothing."""

    code = "invalid_code"
    default_message = "That code is wrong, used or expired. Check it, or ask for a new one."


class CodeRecentlySent(TooManyRequests):
    code = "code_recently_sent"
    default_message = "A code was sent less than a minute ago. Check your inbox first."


class TooManyCodes(TooManyRequests):
    code = "too_many_codes"
    default_message = "Too many codes were requested in the last 24 hours. Try again later."


class InvalidCredentials(Unauthorized):
    """A password was sent and it's wrong, or the account isn't an admin's, or doesn't exist."""

    code = "invalid_credentials"
    default_message = "The family name or password is wrong."
