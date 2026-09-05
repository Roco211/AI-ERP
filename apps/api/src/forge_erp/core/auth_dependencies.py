from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Cookie, Request
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions
from forge_erp.modules.identity.application.commands import resolve_context


async def authenticated_transaction(
    request: Request,
    forge_session: Annotated[str, Cookie()] = "",
) -> AsyncIterator[tuple[AsyncSession, RuntimeContext]]:
    async with sessions.begin() as db:
        ctx = await resolve_context(db, forge_session, request.state.request_id)
        yield db, ctx
