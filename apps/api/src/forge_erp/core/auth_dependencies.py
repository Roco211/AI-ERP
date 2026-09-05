from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Cookie, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions
from forge_erp.modules.identity.application.commands import resolve_context


async def authenticated_transaction(
    request: Request,
    forge_session: Annotated[str, Cookie()] = "",
) -> AsyncIterator[tuple[AsyncSession, RuntimeContext]]:
    # Bind with Depends(..., scope="function"): commit must finish before HTTP success.
    async with sessions.begin() as db:
        ctx = await resolve_context(db, forge_session, request.state.request_id)
        yield db, ctx


async def authenticated_snapshot(
    request: Request,
    forge_session: Annotated[str, Cookie()] = "",
) -> AsyncIterator[tuple[AsyncSession, RuntimeContext]]:
    """One read-only snapshot, established before authentication's first SELECT."""
    async with sessions.begin() as db:
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        ctx = await resolve_context(db, forge_session, request.state.request_id)
        yield db, ctx
