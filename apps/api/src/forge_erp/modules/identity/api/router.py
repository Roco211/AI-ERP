from typing import Annotated

from fastapi import APIRouter, Cookie, Header, Request, Response

from forge_erp.core.config import settings
from forge_erp.core.db import sessions
from forge_erp.core.rate_limit import check_login_rate
from forge_erp.modules.identity.api.schemas import LoginInput, LoginResult, Profile
from forge_erp.modules.identity.application.commands import (
    login_command,
    logout_command,
    profile_query,
    resolve_context,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=LoginResult, operation_id="login")
async def login(
    body: LoginInput,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
) -> LoginResult:
    await check_login_rate(request.client.host if request.client else "unknown")
    token, ttl = await login_command(
        body.organization_code, body.email, body.password, idempotency_key, request.state.request_id
    )
    response.set_cookie(
        "forge_session",
        token,
        max_age=ttl,
        httponly=True,
        secure=settings().cookie_secure,
        samesite="lax",
        path="/",
    )
    return LoginResult()


@router.post("/logout", status_code=204, operation_id="logout")
async def logout(
    request: Request,
    forge_session: Annotated[str, Cookie()] = "",
) -> Response:
    await logout_command(forge_session, request.state.request_id)
    response = Response(status_code=204)
    response.delete_cookie(
        "forge_session", httponly=True, secure=settings().cookie_secure, samesite="lax", path="/"
    )
    return response


@router.get("/me", response_model=Profile, operation_id="getMe")
async def me(
    request: Request,
    forge_session: Annotated[str, Cookie()] = "",
) -> Profile:
    async with sessions.begin() as db:
        ctx = await resolve_context(db, forge_session, request.state.request_id)
        return Profile.model_validate(await profile_query(db, ctx))
