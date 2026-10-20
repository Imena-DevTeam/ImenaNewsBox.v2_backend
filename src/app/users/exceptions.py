"""Exceptions raised by this feature."""

from app.core.exceptions import NotFound


class UserNotFound(NotFound):
    code = "user_not_found"
    default_message = "That member doesn't exist."
