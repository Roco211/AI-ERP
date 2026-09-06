"""Private SSE presentation of the same idempotent graph execution."""

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID

from forge_erp.core.db import sessions
from forge_erp.core.errors import Problem
from forge_erp.modules.assistant.application import runtime, state
from forge_erp.modules.assistant.domain.chat import AssistantStreamEvent, TurnRead


async def events(
    prepared: runtime.Run | TurnRead, token: str, request_id: str, conversation_id: UUID
) -> AsyncGenerator[str]:
    queue: asyncio.Queue[AssistantStreamEvent] = asyncio.Queue(maxsize=64)

    async def execute():
        try:
            if isinstance(prepared, TurnRead):
                result = prepared
            else:
                prepared.emit = queue.put
                async with sessions.begin() as db:
                    ctx = await prepared.authorize(db)
                    initial = await state.read_turn(db, ctx, prepared.turn_id)
                await queue.put(AssistantStreamEvent(type="snapshot", turn=initial))
                result = await runtime.complete_run(prepared)
            await queue.put(AssistantStreamEvent(type="complete", turn=result))
        except Problem as exc:
            await queue.put(
                AssistantStreamEvent(
                    type="error",
                    status=exc.status,
                    code=exc.code,
                    detail=exc.detail,
                    request_id=request_id,
                )
            )
        except Exception:
            await queue.put(
                AssistantStreamEvent(
                    type="error",
                    status=503,
                    code="AI_STREAM_INTERRUPTED",
                    detail="连接中断，请使用原请求重试以确认结果",
                    request_id=request_id,
                )
            )

    task = asyncio.create_task(execute())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=5)
            except TimeoutError:
                item = None
            # Re-check even queued output and heartbeats. No session/transaction spans a wait.
            try:
                async with sessions.begin() as db:
                    ctx = await state.authorize(db, token, request_id, conversation_id)
                    if isinstance(prepared, runtime.Run):
                        row = await state._turn(db, ctx, prepared.turn_id)
                        if row["attempts"] != prepared.attempt:
                            raise Problem(409, "AI_TURN_FENCED", "本次执行已失效，请读取最新结果")
                        if row["state"] == "RUNNING" and (
                            row["lease_until"] is None or row["lease_until"] <= datetime.now(UTC)
                        ):
                            raise Problem(409, "AI_TURN_FENCED", "本次执行已失效，请读取最新结果")
            except Problem as exc:
                item = AssistantStreamEvent(
                    type="error",
                    status=exc.status,
                    code=exc.code,
                    detail=exc.detail,
                    request_id=request_id,
                )
            if item is None:
                yield ": keep-alive\n\n"
                continue
            # Omit unused envelope members only. Nested TurnRead/evidence nulls are
            # part of the same public DTO returned by the JSON endpoints.
            payload = {k: v for k, v in item.model_dump(mode="json").items() if v is not None}
            yield "data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n\n"
            if item.type in {"complete", "error"}:
                return
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
