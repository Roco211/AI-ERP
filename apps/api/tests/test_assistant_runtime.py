"""Fixed model decisions exercise the real graph, PostgreSQL persistence and Query boundary."""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableLambda
from sqlalchemy import text
from test_assistant_provider_settings import grant, save
from test_platform import login

from forge_erp.core.db import sessions, set_tenant
from forge_erp.modules.assistant.application import runtime

BASE = "/api/v1/ai"


@pytest.fixture
async def assistant(client, identities):
    identity = identities[0]
    grant(
        identity,
        {"ai.use", "ai.provider.manage", "catalog.read", "inventory.read", "dashboard.read"},
    )
    assert (await login(client, identity)).status_code == 200
    assert (await save(client)).status_code == 200
    response = await client.post(
        BASE + "/conversations",
        json={"title": "测试对话"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 200, response.text
    return client, identity, response.json()["id"]


def decisions(monkeypatch, values):
    captured = []

    async def model(messages, **kwargs):
        captured.append(messages)
        value = values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(runtime, "decision_runnable", RunnableLambda(model))
    return captured


async def message(client, id, text="查看库存", key=None):
    return await client.post(
        f"{BASE}/conversations/{id}/messages",
        json={"message": text},
        headers={"Idempotency-Key": key or uuid4().hex},
    )


async def test_real_graph_query_checkpoint_and_same_key_no_second_model(assistant, monkeypatch):
    client, identity, id = assistant
    calls = decisions(
        monkeypatch,
        [
            {"action": "query", "tool": "get_inventory", "arguments": {}},
            {"action": "answer", "code": "results", "evidence_ids": ["e1"]},
        ],
    )
    key = uuid4().hex
    response = await message(client, id, key=key)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["state"] == "COMPLETED", data
    assert data["model_calls"] == 2 and data["tool_calls"] == 1
    assert data["evidence"][0]["tool"] == "get_inventory"
    assert len(calls) == 2
    assert "query_results" in str(calls[1])
    retry = await message(client, id, key=key)
    assert retry.json() == data and len(calls) == 2
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        await db.execute(
            text("SELECT set_config('app.user_id',:owner,true)"), {"owner": str(identity["user"])}
        )
        assert (
            await db.execute(text("SELECT count(*) FROM forge.assistant_checkpoints"))
        ).scalar_one() > 0
    history = (await client.get(f"{BASE}/conversations/{id}")).json()
    assert history["turns"][0]["id"] == data["id"]


@pytest.mark.parametrize(
    "malicious",
    [
        {"action": "confirm", "order_id": str(uuid4())},
        {"action": "answer", "code": "results", "evidence_ids": ["made-up"]},
        {"action": "answer", "code": "results", "answer": "库存999999"},
        {
            "action": "query",
            "tool": "get_inventory",
            "arguments": {"organization_id": str(uuid4())},
        },
        {"action": "query", "tool": "post_shipment", "arguments": {}},
    ],
)
async def test_invalid_model_action_cannot_execute_or_publish_facts(
    assistant, monkeypatch, malicious
):
    client, _, id = assistant
    decisions(monkeypatch, [malicious])
    response = await message(client, id)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["state"] == "FAILED", data
    assert not data["evidence"] and data["proposal"] is None
    assert "999999" not in json.dumps(data)


async def test_failed_model_retry_resumes_graph_with_cumulative_budget(assistant, monkeypatch):
    client, _, id = assistant
    from forge_erp.core.errors import Problem

    calls = decisions(
        monkeypatch,
        [
            Problem(503, "AI_PROVIDER_TIMEOUT", "private upstream text"),
            {"action": "answer", "code": "clarify", "missing_fields": ["product"]},
        ],
    )
    first = await message(client, id)
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "FAILED"
    assert first.json()["can_retry"]
    response = await client.post(
        f"{BASE}/turns/{first.json()['id']}/retry", headers={"Idempotency-Key": uuid4().hex}
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "COMPLETED", response.json()
    assert response.json()["model_calls"] == 2
    assert len(calls) == 2
    assert "private upstream" not in response.text


async def test_daily_brief_is_server_facts_without_a_model_call(assistant, monkeypatch):
    client, _, id = assistant
    model = AsyncMock(side_effect=AssertionError("brief needs no model arithmetic"))
    monkeypatch.setattr(runtime, "decision_runnable", model)
    response = await client.post(
        f"{BASE}/conversations/{id}/brief", json={}, headers={"Idempotency-Key": uuid4().hex}
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "COMPLETED", response.json()
    assert response.json()["model_calls"] == 0 and response.json()["tool_calls"] == 1
    assert response.json()["evidence"][0]["tool"] == "get_operating_overview"
    model.assert_not_called()


async def test_model_call_occurs_without_open_database_transaction(assistant, monkeypatch):
    client, _, id = assistant

    async def model(messages, **kwargs):
        async with sessions.begin() as db:
            active = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE usename='forge_app' "
                        "AND state='idle in transaction'"
                    )
                )
            ).scalar_one()
            assert active == 0
        return {"action": "answer", "code": "clarify"}

    monkeypatch.setattr(runtime, "decision_runnable", RunnableLambda(model))
    response = await message(client, id)
    assert response.json()["state"] == "COMPLETED", response.text


def test_ambiguous_shared_product_name_is_not_a_selection():
    first, second = str(uuid4()), str(uuid4())
    payload = {
        "total": 2,
        "items": [
            {"id": first, "sku": "M8", "name": "螺栓"},
            {"id": second, "sku": "M10", "name": "螺栓"},
        ],
    }
    assert not runtime.resolved_candidates("search_products", payload, "开单螺栓7个", [])
    assert runtime.resolved_candidates("search_products", payload, "开单M8螺栓7个", []) == {first}
    assert runtime.resolved_candidates("search_products", payload, "开单螺栓7个", [second]) == {
        second
    }


async def test_provider_change_during_model_response_drops_old_facts(assistant, monkeypatch):
    client, _, id = assistant

    async def model(messages, **kwargs):
        assert (
            await save(client, expected_version=1, model="replacement-model")
        ).status_code == 200
        return {"action": "answer", "code": "clarify"}

    monkeypatch.setattr(runtime, "decision_runnable", RunnableLambda(model))
    response = await message(client, id)
    assert response.status_code == 409
    assert response.json()["code"] == "AI_CONTEXT_CHANGED"
    history = await client.get(f"{BASE}/conversations/{id}")
    assert history.status_code == 409


async def test_tool_budget_is_cumulative_across_model_iterations(assistant, monkeypatch):
    client, _, id = assistant
    decision = {
        "action": "queries",
        "queries": [{"tool": "get_inventory", "arguments": {}} for _ in range(8)],
    }
    calls = decisions(
        monkeypatch, [decision, {"action": "query", "tool": "get_inventory", "arguments": {}}]
    )
    response = await message(client, id)
    assert response.json()["state"] == "FAILED", response.text
    assert response.json()["tool_calls"] == 8
    assert response.json()["error_code"] == "AI_BUDGET_EXCEEDED"
    assert not response.json()["can_retry"]
    assert len(calls) == 2


async def test_metadata_trace_never_sends_input_or_tool_rows(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from pydantic import SecretStr

    from forge_erp.core.context import RuntimeContext
    from forge_erp.modules.assistant.infrastructure import tracing

    client = MagicMock()
    monkeypatch.setattr(tracing, "Client", MagicMock(return_value=client))
    monkeypatch.setattr(
        tracing,
        "settings",
        lambda: SimpleNamespace(
            ai_trace_enabled=True,
            ai_trace_api_key=SecretStr("synthetic-key"),
            ai_trace_project="test",
        ),
    )
    ctx = RuntimeContext(
        uuid4(), uuid4(), frozenset({"ai.use"}), "trace-test", source="AI", conversation_id=uuid4()
    )
    await tracing.record_trace(ctx, uuid4(), "COMPLETED", 2, 1, None)
    captured = client.create_run.call_args.kwargs
    assert captured["inputs"] == {} and captured["outputs"] == {}
    assert set(captured["extra"]["metadata"]) == {
        "request_id",
        "conversation_id",
        "turn_id",
        "status",
        "model_calls",
        "tool_calls",
        "error_code",
    }
    assert "synthetic-key" not in json.dumps(captured, default=str)
    client.close.assert_called_once()


@pytest.mark.parametrize("change", ["logout", "disable", "revoke"])
async def test_authority_change_while_model_waits_cannot_return_old_answer(
    assistant, monkeypatch, change
):
    client, identity, id = assistant
    completed_model = []

    async def model(messages, **kwargs):
        if change == "logout":
            response = await client.post(
                "/api/v1/auth/logout", headers={"Idempotency-Key": uuid4().hex}
            )
            assert response.status_code == 204
        elif change == "revoke":
            grant(identity, {"ai.use", "ai.provider.manage"})
        else:
            from sqlalchemy import create_engine

            from forge_erp.core.config import settings

            with create_engine(settings().migration_database_url).begin() as db:
                db.execute(
                    text(
                        "UPDATE forge.users SET active=false WHERE organization_id=:org "
                        "AND id=:user"
                    ),
                    identity,
                )
        completed_model.append(True)
        return {"action": "answer", "code": "clarify"}

    monkeypatch.setattr(runtime, "decision_runnable", RunnableLambda(model))
    response = await message(client, id)
    assert completed_model == [True]
    assert response.status_code == (409 if change == "revoke" else 401), response.text
    assert "evidence" not in response.json() and "answer" not in response.json()


async def test_whole_turn_deadline_cancels_running_work_and_saves_failure(assistant, monkeypatch):
    import asyncio

    client, _, id = assistant
    original_wait = asyncio.wait_for
    cancelled = False

    async def wait(awaitable, **kwargs):
        deadline = kwargs.get("timeout")
        return await original_wait(awaitable, 0.02 if deadline == 80 else deadline)

    async def operation(self):
        nonlocal cancelled
        try:
            await asyncio.sleep(1)
        finally:
            cancelled = True

    monkeypatch.setattr(runtime.Run, "execute", operation)
    monkeypatch.setattr(runtime.asyncio, "wait_for", wait)
    response = await message(client, id)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "FAILED"
    assert response.json()["error_code"] == "AI_TURN_TIMEOUT"
    assert cancelled
    assert response.json()["can_retry"]
