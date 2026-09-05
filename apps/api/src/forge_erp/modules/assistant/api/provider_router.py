from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.auth_dependencies import authenticated_transaction
from forge_erp.core.context import RuntimeContext
from forge_erp.modules.assistant.application import providers
from forge_erp.modules.assistant.domain.providers import (
    ProviderInput,
    ProviderRead,
    ProviderTestInput,
    ProviderTestRead,
)

Tx = Annotated[
    tuple[AsyncSession, RuntimeContext], Depends(authenticated_transaction, scope="function")
]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
router = APIRouter(prefix="/api/v1/ai/provider", tags=["ai-provider"])


@router.get("", response_model=ProviderRead)
async def get_provider(tx: Tx):
    return await providers.read(*tx)


@router.put("", response_model=ProviderRead)
async def save_provider(body: ProviderInput, tx: Tx, key: Key):
    return await providers.save(*tx, body, key)


@router.post("/test", response_model=ProviderTestRead)
async def test_provider(
    body: ProviderTestInput,
    request: Request,
    key: Key,
    forge_session: Annotated[str, Cookie()] = "",
):
    return await providers.test_connection(
        forge_session, request.state.request_id, body.expected_version, key
    )
