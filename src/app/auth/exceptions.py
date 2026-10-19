"""Exceptions raised by this feature."""

from app.core.exceptions import Forbidden, Unauthorized


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
