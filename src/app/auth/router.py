"""HTTP routes: parse input, call the service, shape the response. No business logic here."""

from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Cookie, Depends, Query, Request, Response

from app.auth import service
from app.auth.dependencies import CurrentAuth, require_allowed_origin, require_owner_or_admin
from app.auth.exceptions import NotAuthenticated
from app.auth.schemas import (
    AccessToken,
    CodeSent,
    GoogleLoginRequest,
    LoginRequest,
    SessionListParams,
    SessionRead,
    VerifyRequest,
)
from app.core.config import get_settings
from app.core.database.session import DbSession
from app.core.email import EmailSender, get_email_sender
from app.core.google import GoogleVerifier, get_google_verifier
from app.core.pagination import Page
from app.core.security import CODE_TTL

router = APIRouter(prefix="/auth", tags=["auth"])

Email = Annotated[EmailSender, Depends(get_email_sender)]
Google = Annotated[GoogleVerifier, Depends(get_google_verifier)]

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


@router.post("/google")
async def google(
    body: GoogleLoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    verifier: Google,
) -> AccessToken:
    """Log this device in with Google instead of an emailed code. Admins add their password."""
    signed_in = await service.google_login(
        db,
        verifier,
        id_token=body.id_token.get_secret_value(),
        password=body.password.get_secret_value() if body.password else None,
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


@router.get("/sessions")
async def list_sessions(
    auth: CurrentAuth, db: DbSession, params: Annotated[SessionListParams, Query()]
) -> Page[SessionRead]:
    """Your logged-in devices, most recently used first. Admins can pass `user_id` to see
    anyone's."""
    owner_id = params.user_id or auth.user.id
    require_owner_or_admin(auth.user, owner_id)
    sessions, total = await service.list_sessions(
        db, owner_id, offset=params.offset, limit=params.limit
    )
    items = [
        SessionRead(
            id=session.id,
            device_label=session.device_label,
            created_at=session.created_at,
            last_used_at=session.last_used_at,
            expires_at=session.expires_at,
            current=session.id == auth.session_id,
        )
        for session in sessions
    ]
    return Page.build(items, params=params, total=total)
