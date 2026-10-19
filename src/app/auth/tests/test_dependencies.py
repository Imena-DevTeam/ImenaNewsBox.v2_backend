import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.auth.dependencies import (
    AdminUser,
    CurrentAuth,
    CurrentUser,
    SocialMediaManagerUser,
    require_owner_or_admin,
)
from app.auth.exceptions import NotOwner
from app.auth.models import UserSession
from app.core.database.session import get_session
from app.core.security import create_access_token
from app.main import create_app
from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]


def _app_with_protected_routes() -> FastAPI:
    app = create_app()

    @app.get("/me")
    async def me(auth: CurrentAuth) -> dict[str, str]:
        return {"user_id": str(auth.user.id), "session_id": str(auth.session_id)}

    @app.get("/posts-admin")
    async def posts_admin(user: SocialMediaManagerUser) -> dict[str, str]:
        return {"role": user.role}

    @app.get("/admin")
    async def admin(user: AdminUser) -> dict[str, str]:
        return {"role": user.role}

    @app.get("/member")
    async def member(user: CurrentUser) -> dict[str, str]:
        return {"role": user.role}

    return app


@pytest.fixture
async def http(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = _app_with_protected_routes()

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _session_of(db_session: AsyncSession, user: User) -> UserSession:
    session = await db_session.scalar(select(UserSession).where(UserSession.user_id == user.id))
    assert session is not None
    return session


# --- Authentication ---


async def test_valid_token_loads_the_user_and_session(
    http: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user()
    headers = await login_as(user)

    response = await http.get("/me", headers=headers)

    assert response.status_code == 200
    session = await _session_of(db_session, user)
    assert response.json() == {"user_id": str(user.id), "session_id": str(session.id)}


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic dXNlcjpwYXNz"}, {"Authorization": "Bearer"}],
    ids=["no-header", "wrong-scheme", "empty-bearer"],
)
async def test_missing_token_is_not_authenticated(
    http: AsyncClient, headers: dict[str, str]
) -> None:
    response = await http.get("/member", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_garbage_token_is_invalid(http: AsyncClient) -> None:
    response = await http.get("/member", headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_expired_token_says_expired(
    http: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user()
    await login_as(user)
    session = await _session_of(db_session, user)
    token, _ = create_access_token(user.id, session.id, now=datetime.now(UTC) - timedelta(hours=1))

    response = await http.get("/member", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"


async def _end_session(db_session: AsyncSession, user: User, **values: Any) -> None:
    await db_session.execute(
        update(UserSession).where(UserSession.user_id == user.id).values(**values)
    )


@pytest.mark.parametrize(
    "change",
    ["revoked", "session_expired", "deactivated"],
)
async def test_ended_sessions_are_rejected_immediately(
    http: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    login_as: LoginAs,
    change: str,
) -> None:
    user = await make_user()
    headers = await login_as(user)
    assert (await http.get("/member", headers=headers)).status_code == 200

    past = datetime.now(UTC) - timedelta(seconds=1)
    if change == "revoked":
        await _end_session(db_session, user, revoked_at=past)
    elif change == "session_expired":
        await _end_session(db_session, user, expires_at=past)
    else:
        user.deactivated_at = past
        await db_session.flush()

    response = await http.get("/member", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_ended"


async def test_a_session_id_from_another_user_is_rejected(
    http: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    alice, bob = await make_user(), await make_user()
    await login_as(bob)
    bobs_session = await _session_of(db_session, bob)
    token, _ = create_access_token(alice.id, bobs_session.id)

    response = await http.get("/member", headers={"Authorization": f"Bearer {token}"})

    assert response.json()["error"]["code"] == "session_ended"


async def test_an_unknown_session_is_rejected(http: AsyncClient, make_user: MakeUser) -> None:
    user = await make_user()
    token, _ = create_access_token(user.id, uuid.uuid7())

    response = await http.get("/member", headers={"Authorization": f"Bearer {token}"})

    assert response.json()["error"]["code"] == "session_ended"


async def test_each_request_runs_exactly_one_query(
    http: AsyncClient, db_connection: AsyncConnection, make_user: MakeUser, login_as: LoginAs
) -> None:
    headers = await login_as(await make_user())
    selects: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(db_connection.sync_engine, "before_cursor_execute", count)
    try:
        assert (await http.get("/member", headers=headers)).status_code == 200
    finally:
        event.remove(db_connection.sync_engine, "before_cursor_execute", count)

    assert len(selects) == 1


# --- Roles ---


@pytest.mark.parametrize(
    ("role", "path", "status"),
    [
        (Role.MEMBER, "/member", 200),
        (Role.MEMBER, "/posts-admin", 403),
        (Role.MEMBER, "/admin", 403),
        (Role.SOCIAL_MEDIA_MANAGER, "/member", 200),
        (Role.SOCIAL_MEDIA_MANAGER, "/posts-admin", 200),
        (Role.SOCIAL_MEDIA_MANAGER, "/admin", 403),
        (Role.ADMIN, "/member", 200),
        (Role.ADMIN, "/posts-admin", 200),
        (Role.ADMIN, "/admin", 200),
    ],
)
async def test_roles_are_cumulative(
    http: AsyncClient,
    make_user: MakeUser,
    login_as: LoginAs,
    role: Role,
    path: str,
    status: int,
) -> None:
    headers = await login_as(await make_user(role=role))

    response = await http.get(path, headers=headers)

    assert response.status_code == status
    if status == 403:
        assert response.json()["error"]["code"] == "insufficient_role"


async def test_role_changes_apply_to_the_next_request(
    http: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    admin = await make_user(role=Role.ADMIN)
    headers = await login_as(admin)
    assert (await http.get("/admin", headers=headers)).status_code == 200

    admin.role = Role.MEMBER
    await db_session.flush()

    assert (await http.get("/admin", headers=headers)).status_code == 403


async def test_wrong_role_is_checked_after_authentication(http: AsyncClient) -> None:
    # No token at all is a 401, never a 403.
    assert (await http.get("/admin")).status_code == 401


# --- Ownership ---


async def test_owner_may_act(make_user: MakeUser) -> None:
    owner = await make_user()

    require_owner_or_admin(owner, owner.id)


async def test_others_may_not(make_user: MakeUser) -> None:
    owner, other = await make_user(), await make_user(role=Role.SOCIAL_MEDIA_MANAGER)

    with pytest.raises(NotOwner) as exc_info:
        require_owner_or_admin(other, owner.id)

    assert exc_info.value.status_code == 403


async def test_admins_may_act_unless_excluded(make_user: MakeUser) -> None:
    owner, admin = await make_user(), await make_user(role=Role.ADMIN)

    require_owner_or_admin(admin, owner.id)
    with pytest.raises(NotOwner):
        require_owner_or_admin(admin, owner.id, allow_admin=False)
