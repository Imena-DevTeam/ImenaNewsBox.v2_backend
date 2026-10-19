"""HTTP routes: parse input, call the service, shape the response. No business logic here."""

from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Cookie, Depends, Request, Response

from app.auth import service
from app.auth.dependencies import require_allowed_origin
from app.auth.exceptions import NotAuthenticated
from app.auth.schemas import AccessToken, CodeSent, LoginRequest, VerifyRequest
from app.core.config import get_settings
from app.core.database.session import DbSession
from app.core.email import EmailSender, get_email_sender
from app.core.security import CODE_TTL

router = APIRouter(prefix="/auth", tags=["auth"])

Email = Annotated[EmailSender, Depends(get_email_sender)]

REFRESH_COOKIE: Final = "refresh_token"
# `main.py` mounts this router under /api/v1, so the browser sends the cookie to the auth
# routes only. There's no `Domain`, so it stays bound to the frontend's host (the proxy).
REFRESH_COOKIE_PATH: Final = "/api/v1/auth"
# The parameter name is the cookie name, so routes must call it `refresh_token`.
RefreshCookie = Annotated[str | None, Cookie()]


def _signed_in(response: Response, signed_in: service.SignedIn) -> AccessToken:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        signed_in.refresh_token,
        max_age=settings.refresh_token_expires_in,
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return AccessToken(
        access_token=signed_in.access_token, expires_in=settings.access_token_expires_in
    )


@router.post("/login")
async def login(body: LoginRequest, db: DbSession, sender: Email) -> CodeSent:
    """Step one on a new device: email a login code. Admins also send their password."""
    password = body.password.get_secret_value() if body.password else None
    await service.start_login(db, sender, family_name=body.family_name, password=password)
    return CodeSent(
        message="If the details are right, a code is on its way.",
        expires_in=int(CODE_TTL.total_seconds()),
        resend_after=int(service.CODE_RESEND_COOLDOWN.total_seconds()),
    )


@router.post("/verify")
async def verify(
    body: VerifyRequest, request: Request, response: Response, db: DbSession
) -> AccessToken:
    """Step two: the emailed code logs this device in and sets the refresh cookie."""
    signed_in = await service.verify_login(
        db,
        family_name=body.family_name,
        code=body.code,
        device_label=service.describe_device(request.headers.get("user-agent")),
    )
    return _signed_in(response, signed_in)


@router.post("/refresh", dependencies=[Depends(require_allowed_origin)])
async def refresh(
    response: Response, db: DbSession, refresh_token: RefreshCookie = None
) -> AccessToken:
    """A new access token for this device, from the refresh cookie (which rotates).

    On 409 `refresh_conflict`, another tab refreshed at the same moment: retry once.
    """
    if refresh_token is None:
        raise NotAuthenticated()
    return _signed_in(response, await service.refresh_session(db, refresh_token))


@router.post(
    "/logout",
    status_code=HTTPStatus.NO_CONTENT,
    dependencies=[Depends(require_allowed_origin)],
)
async def logout(response: Response, db: DbSession, refresh_token: RefreshCookie = None) -> None:
    """Log this device out: revoke its session and clear the cookie. Always succeeds."""
    if refresh_token is not None:
        await service.end_session(db, refresh_token)
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, secure=True, httponly=True, samesite="lax"
    )
