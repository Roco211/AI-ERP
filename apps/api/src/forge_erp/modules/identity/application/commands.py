from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions, set_tenant
from forge_erp.core.errors import Problem
from forge_erp.core.idempotency import execute_once
from forge_erp.core.security import DUMMY_HASH, fingerprint, passwords, token_hash
from forge_erp.modules.audit.service import record_event


async def load_permissions(db: AsyncSession, org: UUID, user: UUID) -> frozenset[str]:
    result = await db.execute(
        text(
            "SELECT DISTINCT rp.permission_code FROM forge.user_roles ur "
            "JOIN forge.role_permissions rp ON rp.organization_id=ur.organization_id "
            "AND rp.role_id=ur.role_id WHERE ur.organization_id=:org AND ur.user_id=:user"
        ),
        {"org": org, "user": user},
    )
    return frozenset(result.scalars())


async def login_command(
    organization_code: str,
    email: str,
    password: str,
    key: str,
    request_id: str,
) -> tuple[str, int]:
    async with sessions.begin() as db:
        candidate = (
            (
                await db.execute(
                    text("SELECT * FROM forge.login_candidate(:code,:email)"),
                    {"code": organization_code.upper(), "email": email.lower()},
                )
            )
            .mappings()
            .first()
        )
        valid = await run_in_threadpool(
            passwords.verify, password, candidate["password_hash"] if candidate else DUMMY_HASH
        )
        if not candidate or not valid:
            raise Problem(401, "INVALID_CREDENTIALS", "Invalid organization, email or password")
        org, user = candidate["organization_id"], candidate["user_id"]
        await set_tenant(db, org)
        ctx = RuntimeContext(org, user, await load_permissions(db, org, user), request_id)
        body = {
            "organization_code": organization_code.upper(),
            "email": email.lower(),
            "password": password,
        }
        token = fingerprint(["session-v1", str(org), str(user), key, fingerprint(body)])

        async def create_session() -> dict[str, Any]:
            row = (
                await db.execute(
                    text(
                        "INSERT INTO forge.sessions(organization_id,user_id,token_hash,expires_at) "
                        "VALUES (:org,:user,:hash,now() + :ttl * interval '1 second') RETURNING id"
                    ),
                    {
                        "org": org,
                        "user": user,
                        "hash": token_hash(token),
                        "ttl": settings().session_ttl_seconds,
                    },
                )
            ).one()
            await record_event(db, ctx, "identity.session.created", row.id)
            return {"session_id": str(row.id)}

        result = await execute_once(db, ctx, "auth.login", key, body, create_session)
        current = (
            await db.execute(
                text(
                    "SELECT expires_at,revoked_at FROM forge.sessions "
                    "WHERE organization_id=:org AND id=:id"
                ),
                {"org": org, "id": UUID(result["session_id"])},
            )
        ).one()
        ttl = int((current.expires_at - datetime.now(UTC)).total_seconds())
        if current.revoked_at or ttl <= 0:
            raise Problem(409, "LOGIN_REPLAY_EXPIRED", "Start a new login attempt")
        return token, ttl


async def resolve_context(db: AsyncSession, token: str, request_id: str) -> RuntimeContext:
    row = (
        (
            await db.execute(
                text("SELECT * FROM forge.resolve_session(:hash)"), {"hash": token_hash(token)}
            )
        )
        .mappings()
        .first()
    )
    if not row:
        raise Problem(401, "UNAUTHENTICATED", "Please sign in")
    org, user = row["organization_id"], row["user_id"]
    await set_tenant(db, org)
    return RuntimeContext(org, user, await load_permissions(db, org, user), request_id)


async def profile_query(db: AsyncSession, ctx: RuntimeContext) -> dict[str, Any]:
    ctx.require("profile.read")
    row = (
        (
            await db.execute(
                text(
                    "SELECT u.id AS user_id,u.organization_id,u.email,u.display_name, "
                    "o.code AS organization_code,o.name AS organization_name FROM forge.users u "
                    "JOIN forge.organizations o ON o.id=u.organization_id "
                    "WHERE u.organization_id=:org AND u.id=:user"
                ),
                {"org": ctx.organization_id, "user": ctx.user_id},
            )
        )
        .mappings()
        .one()
    )
    return {**row, "permissions": sorted(ctx.permissions)}


async def logout_command(token: str, request_id: str) -> None:
    async with sessions.begin() as db:
        try:
            ctx = await resolve_context(db, token, request_id)
        except Problem as exc:
            if exc.status == 401:
                return
            raise
        row = (
            await db.execute(
                text(
                    "UPDATE forge.sessions SET revoked_at=now() WHERE organization_id=:org "
                    "AND token_hash=:hash AND revoked_at IS NULL RETURNING id"
                ),
                {"org": ctx.organization_id, "hash": token_hash(token)},
            )
        ).first()
        if row:
            await record_event(db, ctx, "identity.session.revoked", row.id)
