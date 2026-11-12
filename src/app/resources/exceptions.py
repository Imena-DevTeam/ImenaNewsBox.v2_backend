"""Exceptions raised by this feature."""

from app.core.exceptions import NotFound


class ResourceNotFound(NotFound):
    code = "resource_not_found"
    default_message = "That document doesn't exist."
