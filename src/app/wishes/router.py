"""HTTP routes: parse input, call the service, shape the response. No business logic here."""

from fastapi import APIRouter

router = APIRouter(prefix="/wishes", tags=["wishes"])
