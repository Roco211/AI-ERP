import asyncio
import json
from contextlib import aclosing
from uuid import UUID, uuid4

import pytest
from langchain_core.runnables import RunnableLambda
from sqlalchemy import text
from test_assistant_provider_settings import grant, save
from test_assistant_runtime import assistant as assistant
from test_assistant_runtime import decisions, message
from test_assistant_state import admin_execute
from test_platform import credentials, login

from forge_erp.core.db import sessions
from forge_erp.core.errors import Problem
from forge_erp.main import app
from forge_erp.modules.assistant.application import runtime, state, streaming


def frames(response):
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


def test_stream_openapi_preserves_json_turn_and_registers_event_schema():
    contract = app.openapi()
    for path in (
        "/api/v1/ai/conversations/{id}/messages",
        "/api/v1/ai/conversations/{id}/brief",
        "/api/v1/ai/turns/{turn_id}/retry",
    ):
        content = contract["paths"][path]["post"]["responses"]["200"]["content"]
        assert content["application/json"]["schema"] == {"$ref": "#/components/schemas/TurnRead"}
        assert content["text/event-stream"]["schema"] == {
            "$ref": "#/components/schemas/AssistantStreamEvent"
        }
    assert "AssistantStreamEvent" in contract["components"]["schemas"]


async def test_sse_persists_real_activity_and_exact_key_replay(assistant, monkeypatch):
    client, _, id = assistant
    calls = decisions(
        monkeypatch,
        [
            {"action": "query", "tool": "get_inventory", "arguments": {}},
            {"action": "answer", "code": "results", "evidence_ids": ["e1"]},
        ],
    )
    headers = {"Idempotency-Key": uuid4().hex, "Accept": "text/event-stream"}
    url = f"/api/v1/ai/conversations/{id}/messages"
    response = await client.post(url, json={"message": "查看库存"}, headers=headers)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store, no-transform"
    assert response.headers["X-Accel-Buffering"] == "no"
    events = frames(response)
    final = events[-1]["turn"]
    assert events[-1]["type"] == "complete" and final["state"] == "COMPLETED"
    assert [entry["kind"] for entry in final["activity"]] == ["model", "query", "model"]
    assert all(entry["state"] == "complete" for entry in final["activity"])
    assert any(e.get("turn", {}).get("evidence") for e in events[:-1])
    retry = await client.post(url, json={"message": "查看库存"}, headers=headers)
    assert frames(retry) == [events[-1]] and len(calls) == 2
    assert (await client.get(f"/api/v1/ai/conversations/{id}")).json()["turns"][0] == final


async def test_progress_arrives_while_model_is_waiting_and_disconnect_cancels(
    assistant, monkeypatch
):
    client, _, id = assistant
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def blocked_model(messages, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(runtime, "decision_runnable", RunnableLambda(blocked_model))
    token = client.cookies["forge_session"]
    key = uuid4().hex
    prepared = await runtime.prepare_message(
        token, "stream-test", UUID(id), {"message": "库存"}, key
    )
    iterator = streaming.events(prepared, token, "stream-test", UUID(id))
    first = await anext(iterator)
    assert '"type":"snapshot"' in first and '"state":"RUNNING"' in first
    second = await anext(iterator)
    assert "理解问题" in second
    await asyncio.wait_for(started.wait(), 3)
    await iterator.aclose()
    await asyncio.wait_for(cancelled.wait(), 3)
    # The same request remains a single leased turn, never a second model run.
    replay = await runtime.run_message(token, "stream-replay", UUID(id), {"message": "库存"}, key)
    assert replay.state == "RUNNING" and replay.model_calls == 1


async def test_stream_revalidates_session_before_next_frame(assistant, monkeypatch):
    client, _, id = assistant

    async def blocked_model(messages, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "decision_runnable", RunnableLambda(blocked_model))
    token = client.cookies["forge_session"]
    prepared = await runtime.prepare_message(
        token, "logout-stream", UUID(id), {"message": "库存"}, uuid4().hex
    )
    iterator = streaming.events(prepared, token, "logout-stream", UUID(id))
    await anext(iterator)
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    event = json.loads((await anext(iterator))[6:])
    assert event["type"] == "error" and event["status"] == 401
    assert "turn" not in event
    await iterator.aclose()


async def test_casual_stream_guides_on_third_and_business_resets(assistant, monkeypatch):
    client, _, id = assistant
    decisions(
        monkeypatch,
        [{"action": "chat"}] * 3
        + [
            {"action": "answer", "code": "clarify", "missing_fields": ["product"]},
            {"action": "chat"},
        ],
    )
    captured = []

    async def chat(messages, connection):
        captured.append(messages)
        yield "你好，很高兴和你聊聊。"
        yield "今天有什么有趣的事情吗？"

    monkeypatch.setattr(runtime, "stream_chat", chat)
    for index in range(3):
        response = await client.post(
            f"/api/v1/ai/conversations/{id}/messages",
            json={"message": "聊聊天"},
            headers={"Idempotency-Key": uuid4().hex, "Accept": "text/event-stream"},
        )
        events = frames(response)
        final = events[-1]["turn"]
        assert final["state"] == "COMPLETED" and final["interaction"] == "casual"
        assert final["guided"] is (index == 2)
        assert final["tool_calls"] == 0 and final["model_calls"] == 2
        assert "".join(e.get("delta", "") for e in events) == final["answer"]
        assert (runtime.GUIDANCE in final["answer"]) is (index == 2)
    business = await message(client, id, "查库存")
    assert business.json()["interaction"] == "business"
    after = await message(client, id, "谢谢，聊聊天")
    assert after.json()["guided"] is False
    assert "query_results" not in str(captured) and "evidence" not in str(captured)


async def test_partial_reply_failure_does_not_commit_success(assistant, monkeypatch):
    client, _, id = assistant
    decisions(monkeypatch, [{"action": "chat"}])

    async def chat(messages, connection):
        yield "你好。" * 10
        raise Problem(503, "AI_PROVIDER_CONNECTION", "private upstream text")

    monkeypatch.setattr(runtime, "stream_chat", chat)
    response = await client.post(
        f"/api/v1/ai/conversations/{id}/messages",
        json={"message": "你好"},
        headers={"Idempotency-Key": uuid4().hex, "Accept": "text/event-stream"},
    )
    events = frames(response)
    assert any(e["type"] == "delta" for e in events)
    final = events[-1]["turn"]
    assert final["state"] == "FAILED" and not final["answer"] and final["can_retry"]
    assert final["activity"][-1]["state"] == "failed"
    assert "private upstream text" not in response.text


@pytest.mark.parametrize("change", ["permissions", "provider", "lease"])
async def test_queued_stream_frames_recheck_context_and_lease(assistant, monkeypatch, change):
    client, identity, id = assistant

    async def blocked_model(messages, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "decision_runnable", RunnableLambda(blocked_model))
    token = client.cookies["forge_session"]
    prepared = await runtime.prepare_message(
        token, "queued-stream", UUID(id), {"message": "库存"}, uuid4().hex
    )
    assert isinstance(prepared, runtime.Run)
    async with aclosing(streaming.events(prepared, token, "queued-stream", UUID(id))) as iterator:
        await anext(iterator)
        if change == "permissions":
            grant(identity, {"ai.use", "catalog.read"})
        elif change == "provider":
            assert (
                await save(client, expected_version=1, model="changed-test-model")
            ).status_code == 200
        else:
            admin_execute(
                "UPDATE forge.assistant_turns SET lease_until=now()-interval '1 second' "
                "WHERE id=:id",
                {"id": prepared.turn_id},
            )
        event = json.loads((await anext(iterator))[6:])
        assert event["type"] == "error" and "turn" not in event and "delta" not in event
        assert event["code"] == ("AI_TURN_FENCED" if change == "lease" else "AI_CONTEXT_CHANGED")


async def test_retry_closes_interrupted_activity_and_preserves_original_turn(
    assistant, monkeypatch
):
    client, _, id = assistant
    token = client.cookies["forge_session"]
    key = uuid4().hex
    prepared = await runtime.prepare_message(
        token, "old-attempt", UUID(id), {"message": "库存"}, key
    )
    assert isinstance(prepared, runtime.Run)
    old_activity = await prepared.activity("理解问题与选择业务能力", "model")
    admin_execute(
        "UPDATE forge.assistant_turns SET lease_until=now()-interval '1 second' WHERE id=:id",
        {"id": prepared.turn_id},
    )
    resumed = await runtime.prepare_retry(token, "new-attempt", prepared.turn_id)
    assert isinstance(resumed, runtime.Run) and resumed.turn_id == prepared.turn_id
    with pytest.raises(Problem) as exc:
        await prepared.text_delta("失效执行不得输出。")
    assert exc.value.code == "AI_TURN_FENCED"
    decisions(monkeypatch, [{"action": "answer", "code": "clarify"}])
    final = await runtime.complete_run(resumed)
    previous = next(x for x in final.activity if x.id == old_activity.id)
    assert previous.state == "failed" and previous.finished_at is not None
    assert final.state == "COMPLETED"
    replay = await runtime.prepare_message(token, "replay", UUID(id), {"message": "库存"}, key)
    assert replay == final


async def test_chat_model_wait_has_no_open_database_transaction(assistant, monkeypatch):
    client, _, id = assistant
    decisions(monkeypatch, [{"action": "chat"}])

    async def chat(messages, connection):
        async with sessions.begin() as db:
            count = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE usename='forge_app' "
                        "AND state='idle in transaction'"
                    )
                )
            ).scalar_one()
            assert count == 0
        yield "我有2个轻松的聊天话题。"

    monkeypatch.setattr(runtime, "stream_chat", chat)
    result = await message(client, id, "聊聊天吧")
    assert result.json()["state"] == "COMPLETED"
    assert "2个" in result.json()["answer"]


@pytest.mark.parametrize("scope", ["tenant", "owner"])
async def test_foreign_tenant_or_owner_cannot_open_a_turn_stream(
    assistant, identities, monkeypatch, password_hash, scope
):
    client, identity, id = assistant
    decisions(monkeypatch, [{"action": "answer", "code": "clarify"}])
    own = (await message(client, id)).json()
    if scope == "tenant":
        grant(identities[1], {"ai.use"})
        assert (await login(client, identities[1])).status_code == 200
    else:
        other = uuid4()
        admin_execute(
            "INSERT INTO forge.users(id,organization_id,email,display_name,password_hash) "
            "VALUES(:id,:org,'stream-other@example.test','Other',:hash)",
            {"id": other, "org": identity["org"], "hash": password_hash},
        )
        admin_execute(
            "INSERT INTO forge.user_roles(organization_id,user_id,role_id) "
            "VALUES(:org,:user,:role)",
            {"org": identity["org"], "user": other, "role": identity["role"]},
        )
        auth = await client.post(
            "/api/v1/auth/login",
            json={**credentials(identity), "email": "stream-other@example.test"},
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert auth.status_code == 200
    async with sessions.begin() as db:
        await state.authorize(db, client.cookies["forge_session"], "stream-owner-rls")
        assert (
            await db.execute(text("SELECT count(*) FROM forge.assistant_turns"))
        ).scalar_one() == 0
    for endpoint, body in [
        (f"/api/v1/ai/conversations/{id}/messages", {"message": "继续"}),
        (f"/api/v1/ai/turns/{own['id']}/retry", None),
    ]:
        response = await client.post(
            endpoint,
            json=body,
            headers={"Idempotency-Key": uuid4().hex, "Accept": "text/event-stream"},
        )
        assert (
            response.status_code == 404
            and "text/event-stream" not in response.headers["content-type"]
        )
        assert "activity" not in response.text and own["id"] not in response.text


async def test_query_cannot_switch_to_unproven_chat(assistant, monkeypatch):
    client, _, id = assistant
    decisions(
        monkeypatch,
        [
            {"action": "query", "tool": "get_inventory", "arguments": {}},
            {"action": "chat"},
        ],
    )

    async def never_chat(*args):
        raise AssertionError("Business facts must stay in validated query results")
        yield

    monkeypatch.setattr(runtime, "stream_chat", never_chat)
    result = await message(client, id)
    assert result.json()["state"] == "FAILED"
    assert result.json()["error_code"] == "AI_DECISION_INVALID"


async def test_failed_business_request_breaks_consecutive_casual_turns(assistant, monkeypatch):
    client, _, id = assistant
    decisions(
        monkeypatch,
        [
            {"action": "chat"},
            {"action": "chat"},
            Problem(503, "AI_PROVIDER_TIMEOUT", "test-only failure"),
            {"action": "chat"},
        ],
    )

    async def chat(messages, connection):
        yield "可以，今天有什么趣事吗？"

    monkeypatch.setattr(runtime, "stream_chat", chat)
    for _ in range(2):
        assert (await message(client, id, "聊聊今天吧")).json()["interaction"] == "casual"
    assert (await message(client, id, "查询库存数量")).json()["state"] == "FAILED"
    final = (await message(client, id, "继续聊吧")).json()
    assert final["interaction"] == "casual" and final["guided"] is False


async def test_daily_brief_stream_publishes_real_query_and_exact_replay(assistant, monkeypatch):
    client, _, id = assistant

    async def never_model(*args, **kwargs):
        raise AssertionError("Daily brief only runs the existing server query")

    monkeypatch.setattr(runtime, "decision_runnable", RunnableLambda(never_model))
    endpoint = f"/api/v1/ai/conversations/{id}/brief"
    headers = {"Idempotency-Key": uuid4().hex, "Accept": "text/event-stream"}
    events = frames(await client.post(endpoint, json={}, headers=headers))
    turn = events[-1]["turn"]
    assert turn["state"] == "COMPLETED" and turn["model_calls"] == 0
    assert [item["kind"] for item in turn["activity"]] == ["query"]
    assert turn["activity"][0]["state"] == "complete"
    assert turn["evidence"][0]["tool"] == "get_operating_overview"
    assert not any(event["type"] == "delta" for event in events)
    assert frames(await client.post(endpoint, json={}, headers=headers)) == [events[-1]]
