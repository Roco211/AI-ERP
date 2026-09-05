from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions
from forge_erp.modules.assistant.application import providers, runtime, state
from forge_erp.modules.assistant.domain.chat import (
    AssistantStatus,
    BriefInput,
    ConversationDeleted,
    ConversationInput,
    ConversationRead,
    ConversationsPage,
    ConversationSummary,
    DraftCreationReceipt,
    MessageInput,
    ProposalApproval,
    ProposalEdit,
    ProposalRead,
    ProposalRejection,
    TurnRead,
)

router = APIRouter(prefix="/api/v1/ai", tags=["ai-assistant"])
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
CookieToken = Annotated[str, Cookie(alias="forge_session")]


async def transaction(
    request: Request, forge_session: CookieToken = ""
) -> AsyncIterator[tuple[AsyncSession, RuntimeContext]]:
    async with sessions.begin() as db:
        ctx = await state.authorize(db, forge_session, request.state.request_id)
        yield db, ctx


Tx = Annotated[tuple[AsyncSession, RuntimeContext], Depends(transaction, scope="function")]


@router.get("/status", response_model=AssistantStatus)
async def status(tx: Tx):
    db, ctx = tx
    value = await providers.row(db, ctx)
    return AssistantStatus(
        configured=bool(value and value["enabled"]),
        provider_name=value["name"] if value else None,
        model=value["model"] if value else None,
        can_manage_provider="ai.provider.manage" in ctx.permissions,
    )


@router.get("/conversations", response_model=ConversationsPage)
async def conversations(
    tx: Tx, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=25)
):
    return await state.list_conversations(*tx, page, page_size)


@router.post("/conversations", response_model=ConversationSummary)
async def create_conversation(body: ConversationInput, tx: Tx, key: Key):
    return await state.create_conversation(*tx, body, key)


@router.get("/conversations/{id}", response_model=ConversationRead)
async def conversation(id: UUID, tx: Tx):
    return await state.conversation_detail(*tx, id)


@router.delete("/conversations/{id}", response_model=ConversationDeleted)
async def delete_conversation(id: UUID, tx: Tx, key: Key):
    return await state.delete_conversation(*tx, id, key)


@router.post("/conversations/{id}/messages", response_model=TurnRead)
async def message(
    id: UUID, body: MessageInput, request: Request, key: Key, forge_session: CookieToken = ""
):
    return await runtime.run_message(
        forge_session, request.state.request_id, id, body.model_dump(mode="json"), key
    )


@router.post("/conversations/{id}/brief", response_model=TurnRead)
async def brief(
    id: UUID, body: BriefInput, request: Request, key: Key, forge_session: CookieToken = ""
):
    return await runtime.run_message(
        forge_session, request.state.request_id, id, body.model_dump(mode="json"), key, kind="BRIEF"
    )


@router.post("/turns/{turn_id}/retry", response_model=TurnRead)
async def retry(turn_id: UUID, request: Request, key: Key, forge_session: CookieToken = ""):
    return await runtime.retry_turn(forge_session, request.state.request_id, turn_id, key)


@router.post("/proposals/{id}/preview", response_model=ProposalRead)
async def preview(id: UUID, body: ProposalEdit, tx: Tx, key: Key):
    return await state.edit_proposal(*tx, id, body, key)


@router.post("/proposals/{id}/approve", response_model=DraftCreationReceipt)
async def approve(
    id: UUID, body: ProposalApproval, request: Request, key: Key, forge_session: CookieToken = ""
):
    async with sessions.begin() as db:
        ctx = await state.authorize(db, forge_session, request.state.request_id)
        proposal = await state.approve_proposal(db, ctx, id, body, key)
    # The permanent business receipt has committed before checkpoint continuation.
    await runtime.resume_review(forge_session, request.state.request_id, proposal.turn_id)
    return proposal.receipt


@router.post("/proposals/{id}/reject", response_model=ProposalRead)
async def reject(
    id: UUID, body: ProposalRejection, request: Request, key: Key, forge_session: CookieToken = ""
):
    async with sessions.begin() as db:
        ctx = await state.authorize(db, forge_session, request.state.request_id)
        proposal = await state.reject_proposal(db, ctx, id, body, key)
    await runtime.resume_review(forge_session, request.state.request_id, proposal.turn_id)
    return proposal
