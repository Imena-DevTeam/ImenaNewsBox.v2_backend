"""HTTP routes: parse input, call the service, shape the response. No business logic here."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth import service
from app.auth.schemas import CodeSent, LoginRequest
from app.core.database.session import DbSession
from app.core.email import EmailSender, get_email_sender
from app.core.security import CODE_TTL

router = APIRouter(prefix="/auth", tags=["auth"])

Email = Annotated[EmailSender, Depends(get_email_sender)]


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
