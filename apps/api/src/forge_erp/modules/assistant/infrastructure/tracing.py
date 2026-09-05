"""Opt-in trace metadata only; no prompts, query rows, cookies or model credentials."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from langsmith import Client

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext

log = structlog.get_logger()


async def record_trace(
    ctx: RuntimeContext,
    turn_id: UUID,
    status: str,
    model_calls: int,
    tool_calls: int,
    error_code: str | None,
) -> None:
    metadata = {
        "request_id": ctx.request_id,
        "conversation_id": str(ctx.conversation_id),
        "turn_id": str(turn_id),
        "status": status,
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "error_code": error_code,
    }
    log.info("ai_turn", **metadata)
    cfg = settings()
    if not cfg.ai_trace_enabled:
        return

    def send():
        client = Client(
            api_key=cfg.ai_trace_api_key.get_secret_value(),
            timeout_ms=1000,
            auto_batch_tracing=False,
        )
        try:
            client.create_run(
                name="forge.assistant.turn",
                run_type="chain",
                id=uuid4(),
                inputs={},
                outputs={},
                extra={"metadata": metadata},
                project_name=cfg.ai_trace_project,
                start_time=datetime.now(UTC),
                end_time=datetime.now(UTC),
            )
        finally:
            client.close()

    try:
        await asyncio.wait_for(asyncio.to_thread(send), timeout=2)
    except Exception as exc:
        log.warning("ai_trace_unavailable", error_type=type(exc).__name__)
