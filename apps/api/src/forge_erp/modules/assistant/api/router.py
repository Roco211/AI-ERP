from collections.abc import AsyncGenerator, AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Receive, Scope, Send

from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions
from forge_erp.modules.assistant.application import providers, runtime, state, streaming
from forge_erp.modules.assistant.domain.chat import (
    AssistantStatus,
    AssistantStreamEvent,
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
STREAM_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {
        "model": AssistantStreamEvent,
        "content": {
            "text/event-stream": {"schema": {"$ref": "#/components/schemas/AssistantStreamEvent"}}
        },
    }
}
# Register the SSE event model with FastAPI while retaining the exact JSON
# response contract for existing generated clients. FastAPI's additional-model
# handling otherwise applies the SSE envelope model to application/json too.
STREAM_OPENAPI = {
    "responses": {
        "200": {
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TurnRead"}}}
        }
    }
}


class AssistantEventResponse(StreamingResponse):
    def __init__(self, events: AsyncGenerator[str]):
        self.events = events
        super().__init__(
            events,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"},
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # A send-side disconnect can happen while the generator is paused
            # after yielding. Explicit closure cancels its graph task in both
            # Starlette's ASGI 2.3 cancellation and ASGI 2.4 OSError branches.
            await self.events.aclose()


def event_response(prepared, token, request_id, conversation_id):
    return AssistantEventResponse(streaming.events(prepared, token, request_id, conversation_id))


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


@router.post(
    "/conversations/{id}/messages",
    response_model=TurnRead,
    responses=STREAM_RESPONSE,
    openapi_extra=STREAM_OPENAPI,
)
async def message(
    id: UUID, body: MessageInput, request: Request, key: Key, forge_session: CookieToken = ""
):
    if "text/event-stream" in request.headers.get("accept", ""):
        prepared = await runtime.prepare_message(
            forge_session, request.state.request_id, id, body.model_dump(mode="json"), key
        )
        return event_response(prepared, forge_session, request.state.request_id, id)
    return await runtime.run_message(
        forge_session, request.state.request_id, id, body.model_dump(mode="json"), key
    )


@router.post(
    "/conversations/{id}/brief",
    response_model=TurnRead,
    responses=STREAM_RESPONSE,
    openapi_extra=STREAM_OPENAPI,
)
async def brief(
    id: UUID, body: BriefInput, request: Request, key: Key, forge_session: CookieToken = ""
):
    if "text/event-stream" in request.headers.get("accept", ""):
        prepared = await runtime.prepare_message(
            forge_session,
            request.state.request_id,
            id,
            body.model_dump(mode="json"),
            key,
            kind="BRIEF",
        )
        return event_response(prepared, forge_session, request.state.request_id, id)
    return await runtime.run_message(
        forge_session, request.state.request_id, id, body.model_dump(mode="json"), key, kind="BRIEF"
    )


@router.post(
    "/turns/{turn_id}/retry",
    response_model=TurnRead,
    responses=STREAM_RESPONSE,
    openapi_extra=STREAM_OPENAPI,
)
async def retry(turn_id: UUID, request: Request, key: Key, forge_session: CookieToken = ""):
    if "text/event-stream" in request.headers.get("accept", ""):
        prepared = await runtime.prepare_retry(forge_session, request.state.request_id, turn_id)
        async with sessions.begin() as db:
            ctx = await state.authorize(db, forge_session, request.state.request_id)
            row = await state._turn(db, ctx, turn_id)
        return event_response(
            prepared, forge_session, request.state.request_id, row["conversation_id"]
        )
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
