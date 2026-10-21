"""Exceptions raised by this feature."""

from app.core.exceptions import BadRequest, Conflict, NotFound


class WishNotFound(NotFound):
    code = "wish_not_found"
    default_message = "That wish doesn't exist."


class CannotWishYourself(BadRequest):
    code = "cannot_wish_yourself"
    default_message = "You can't send a birthday wish to yourself."


class BirthdayNotSoon(Conflict):
    """Also when the recipient hasn't given a birthday."""

    code = "birthday_not_soon"
    default_message = "You can only wish members whose birthday is today or in the next 30 days."


class WishAlreadySent(Conflict):
    code = "wish_already_sent"
    default_message = "You already wished this member a happy birthday this year."
