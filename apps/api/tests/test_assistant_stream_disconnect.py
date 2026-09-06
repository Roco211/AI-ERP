"""Exercise the real response and SSE generator lifecycle without a database/model."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import ClientDisconnect

from forge_erp.modules.assistant.api.router import event_response
from forge_erp.modules.assistant.application import runtime, streaming
from forge_erp.modules.assistant.domain.chat import TurnRead


@pytest.mark.parametrize("spec", ["2.3", "2.4"])
async def test_response_disconnect_closes_stream_and_cancels_graph(monkeypatch, spec):
    started, cancelled = asyncio.Event(), asyncio.Event()
    turn_id, conversation_id = uuid4(), uuid4()

    class Prepared:
        attempt = 1

        def __init__(self):
            self.turn_id = turn_id

        async def authorize(self, db):
            return None

    @asynccontextmanager
    async def begin():
        yield None

    async def authorize(*args):
        return None

    async def read_turn(*args):
        return TurnRead(
            id=turn_id,
            state="RUNNING",
            prompt="synthetic-only",
            created_at=datetime.now(UTC),
            answer=None,
            evidence=[],
            proposal=None,
            error_code=None,
            error_message=None,
            model_calls=1,
            tool_calls=0,
            can_retry=False,
        )

    async def row(*args):
        return {
            "attempts": 1,
            "state": "RUNNING",
            "lease_until": datetime.now(UTC) + timedelta(seconds=120),
        }

    async def complete_run(prepared):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(runtime, "Run", Prepared)
    monkeypatch.setattr(runtime, "complete_run", complete_run)
    monkeypatch.setattr(streaming, "sessions", SimpleNamespace(begin=begin))
    monkeypatch.setattr(streaming.state, "authorize", authorize)
    monkeypatch.setattr(streaming.state, "read_turn", read_turn)
    monkeypatch.setattr(streaming.state, "_turn", row)
    response = event_response(Prepared(), "synthetic-token", "disconnect-test", conversation_id)

    async def receive():
        await started.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if spec == "2.4" and message["type"] == "http.response.body":
            await started.wait()
            raise OSError("synthetic disconnected socket")

    scope = {"type": "http", "asgi": {"spec_version": spec}}
    if spec == "2.4":
        with pytest.raises(ClientDisconnect):
            await response(scope, receive, send)
    else:
        await response(scope, receive, send)
    assert started.is_set() and cancelled.is_set()
    assert response.events.ag_frame is None
