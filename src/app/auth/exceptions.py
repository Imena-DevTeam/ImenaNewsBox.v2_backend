"""Exceptions raised by this feature."""

from fastapi import HTTPException, status


class InvalidFamilyName(HTTPException):
    def __init__(self) -> None:
        super.__init__(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid family name.",
            headers={"WWW-Authenticate": "Bearer"},
        )


class NotAuthenticated(HTTPException):
    def __init__(self) -> None:
        super.__init__(
            status.HTTP_401_UNAUTHORIZED,
            "You have not been authenticated nigga.",
            headers={"WWW-Authenticate": "Bearer"},
        )


class InvalidToken(HTTPException):
    def __init__(self) -> None:
        super.__init__(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


class InvalidOtpError(HTTPException):
    def __init__() -> None:
        super.__init__(status.HTTP_400_BAD_REQUEST, "Invalid or expired otp code.")


class OAuthTokenError(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_401_UNAUTHORIZED,
            "Google sign-in failed",
            headers={"WWW-Authenticate": "Bearer"},
        )


class ForbiddenOriginError(HTTPException):
    def __init__(self) -> None:
        super().__init__(status.HTTP_403_FORBIDDEN, "Origin not allowed")
