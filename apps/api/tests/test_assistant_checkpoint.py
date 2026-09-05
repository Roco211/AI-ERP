"""Real PostgreSQL persistence and a closed, non-executable checkpoint codec."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from typing import TypedDict
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Interrupt, interrupt
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.errors import Problem
from forge_erp.modules.assistant.domain.checkpoint import CheckpointJSONCodec
from forge_erp.modules.assistant.infrastructure.checkpointer import PostgreSQLCheckpointSaver

AI_TABLES = (
    "assistant_conversations",
    "assistant_turns",
    "assistant_proposals",
    "assistant_draft_receipts",
    "assistant_checkpoints",
    "assistant_checkpoint_writes",
)


@dataclass
class AssistantScope:
    context: RuntimeContext
    conversation: object
    turn: object
    other_user: object
    sessions: async_sessionmaker[AsyncSession]
    saver: PostgreSQLCheckpointSaver


@pytest.fixture
async def assistant_scope(identities, password_hash) -> AsyncIterator[AssistantScope]:
    identity = identities[0]
    org, owner = identity["org"], identity["user"]
    conversation, turn, other_user = uuid4(), uuid4(), uuid4()
    admin = create_engine(settings().migration_database_url)
    with admin.begin() as db:
        db.execute(
            text(
                "INSERT INTO forge.users(id,organization_id,email,display_name,password_hash) "
                "VALUES (:id,:org,'other@example.test','Other User',:hash)"
            ),
            {"id": other_user, "org": org, "hash": password_hash},
        )
        db.execute(
            text(
                "INSERT INTO forge.assistant_conversations "
                "(id,organization_id,owner_id,permissions_hash) VALUES (:id,:org,:owner,:hash)"
            ),
            {"id": conversation, "org": org, "owner": owner, "hash": "a" * 64},
        )
        db.execute(
            text(
                "INSERT INTO forge.assistant_turns "
                "(id,organization_id,owner_id,conversation_id,idempotency_key,request_hash,"
                "prompt,request_id) VALUES (:id,:org,:owner,:conversation,'test-turn-key',"
                ":hash,'生成两件商品的草稿预览','checkpoint-test')"
            ),
            {
                "id": turn,
                "org": org,
                "owner": owner,
                "conversation": conversation,
                "hash": "b" * 64,
            },
        )
    db_engine = create_async_engine(settings().database_url)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    context = RuntimeContext(org, owner, frozenset({"ai.use"}), "checkpoint-test", "AI")
    saver = PostgreSQLCheckpointSaver(factory, context, conversation, turn)
    yield AssistantScope(context, conversation, turn, other_user, factory, saver)
    await db_engine.dispose()
    # Only the fixture-created TEST organization; leave cleanup independent from
    # conftest's evolving table list and never touch pre-existing app data.
    with admin.begin() as db:
        db.execute(text("SET LOCAL session_replication_role=replica"))
        for table in reversed(AI_TABLES):
            db.execute(text(f"DELETE FROM forge.{table} WHERE organization_id=:org"), {"org": org})
    admin.dispose()


def test_checkpoint_codec_round_trip_interrupt_and_tag_shaped_business_dict():
    codec = CheckpointJSONCodec()
    original = {
        "amount": "12.340000",
        "literal": ["interrupt", "fake", {"module": "os", "call": "system"}],
        "mapping": {"version": 1, "value": ["interrupt", "fake", "text"]},
        "pending": (Interrupt({"proposal": "review-me"}, id="known-id"),),
        "empty": None,
        "enabled": True,
    }
    restored = codec.loads_typed(codec.dumps_typed(original))
    assert restored == original
    assert type(restored["literal"]) is list
    assert type(restored["mapping"]) is dict
    assert type(restored["pending"]) is tuple
    assert type(restored["pending"][0]) is Interrupt


@pytest.mark.parametrize(
    "value",
    [
        Decimal("2.00"),
        uuid4(),
        1.25,
        b"pickle-content",
        {"bad": {1, 2}},
        {1: "non-string key"},
        HumanMessage(content="SDK messages must be JSON DTOs"),
        RuntimeContext(uuid4(), uuid4(), frozenset({"ai.use"}), "private", "AI"),
    ],
)
def test_checkpoint_codec_rejects_non_json_and_authorization_objects(value):
    with pytest.raises(ValueError, match="unsupported|requires string"):
        CheckpointJSONCodec().dumps_typed(value)


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "value": ["object", "os", "system", "arbitrary command"]},
        {"version": 1, "value": {"type": "pickle", "data": "not decoded"}},
        {"version": 1, "value": ["dict", [["x", 1], ["x", 2]]]},
        {"version": 1, "value": ["interrupt", {}, "bad-id"]},
        {"version": 1, "value": float("nan")},
        {"version": True, "value": "bad version"},
    ],
)
def test_checkpoint_codec_rejects_unknown_or_malformed_tags(payload):
    encoded = json.dumps(payload).encode()
    with pytest.raises(ValueError):
        CheckpointJSONCodec().loads_typed(("forge-json-v1", encoded))


def test_checkpoint_codec_rejects_pickle_size_depth_and_null_characters():
    codec = CheckpointJSONCodec()
    with pytest.raises(ValueError):
        codec.loads_typed(("pickle", b"untrusted bytes"))
    with pytest.raises(ValueError, match="size"):
        codec.dumps_typed("x" * 1_048_576)
    nested: list = []
    for _ in range(66):
        nested = [nested]
    with pytest.raises(ValueError, match="structure"):
        codec.dumps_typed(nested)
    with pytest.raises(ValueError, match="null"):
        codec.dumps_typed("\x00")


class ReviewState(TypedDict):
    prompt: str
    decision: str


def _review_graph(saver):
    def review(state: ReviewState):
        response = interrupt({"proposal_id": "resolved-proposal", "quantity": "2.000000"})
        return {"decision": response["decision"]}

    builder = StateGraph(ReviewState)
    builder.add_node("review", review)
    builder.add_edge(START, "review")
    builder.add_edge("review", END)
    return builder.compile(checkpointer=saver)


async def test_checkpoint_actual_langgraph_interrupt_survives_new_saver_and_connection(
    assistant_scope,
):
    scope = assistant_scope
    first = _review_graph(scope.saver)
    result = await first.ainvoke({"prompt": "复核草稿", "decision": ""}, scope.saver.config())
    assert result["__interrupt__"][0].value["quantity"] == "2.000000"
    checkpoint = await scope.saver.aget_tuple(scope.saver.config())
    assert checkpoint is not None
    assert any(channel == "__interrupt__" for _, channel, _ in checkpoint.pending_writes)

    # No saver or DB connection from the first graph is reused for resumption.
    reader_engine = create_async_engine(settings().database_url)
    try:
        restored_saver = PostgreSQLCheckpointSaver(
            async_sessionmaker(reader_engine, expire_on_commit=False),
            scope.context,
            scope.conversation,
            scope.turn,
        )
        restored_graph = _review_graph(restored_saver)
        state = await restored_graph.aget_state(restored_saver.config())
        assert state.next == ("review",)
        assert state.tasks[0].interrupts[0].value["proposal_id"] == "resolved-proposal"
        completed = await restored_graph.ainvoke(
            Command(resume={"decision": "user-approved"}), restored_saver.config()
        )
        assert completed["decision"] == "user-approved"
        assert "__interrupt__" not in completed
        history = [item async for item in restored_saver.alist(None)]
        assert len(history) >= 3
        assert history[0].parent_config is not None
        assert history[0].checkpoint["channel_values"]["decision"] == "user-approved"
    finally:
        await reader_engine.dispose()


async def test_checkpoint_pending_writes_retry_semantics_and_history(assistant_scope):
    saver = assistant_scope.saver
    first = empty_checkpoint()
    first_config = await saver.aput(saver.config(), first, {"source": "input", "step": -1}, {})
    await saver.aput_writes(first_config, [("result", {"amount": "10.00"})], "task-1")
    await saver.aput_writes(first_config, [("result", {"amount": "999.00"})], "task-1")
    await saver.aput_writes(first_config, [("__error__", "first failure")], "task-2")
    await saver.aput_writes(first_config, [("__error__", "last failure")], "task-2")
    saved = await saver.aget_tuple(first_config)
    assert saved is not None
    assert saved.pending_writes == [
        ("task-1", "result", {"amount": "10.00"}),
        ("task-2", "__error__", "last failure"),
    ]
    second = empty_checkpoint()
    second_config = await saver.aput(first_config, second, {"source": "loop", "step": 0}, {})
    before = [item async for item in saver.alist(None, before=second_config, limit=1)]
    assert [item.checkpoint["id"] for item in before] == [first["id"]]
    filtered = [item async for item in saver.alist(None, filter={"source": "loop"})]
    assert [item.checkpoint["id"] for item in filtered] == [second["id"]]
    await saver.adelete_thread(str(assistant_scope.turn))
    assert await saver.aget_tuple(saver.config()) is None


async def test_checkpoint_ignores_untrusted_config_identity_metadata(assistant_scope):
    saver = assistant_scope.saver
    config = saver.config()
    config["configurable"].update(
        {"organization_id": str(uuid4()), "owner_id": str(uuid4()), "permissions": "admin"}
    )
    config["metadata"] = {"cookie": "secret-never-persist", "api_key": "secret-never-persist"}
    checkpoint = empty_checkpoint()
    await saver.aput(config, checkpoint, {"source": "input", "step": -1}, {})
    restored = await saver.aget_tuple(saver.config())
    assert restored is not None
    assert restored.metadata == {"source": "input", "step": -1}
    assert set(restored.config["configurable"]) == {"thread_id", "checkpoint_ns", "checkpoint_id"}


async def test_checkpoint_cannot_select_another_thread_or_namespace(assistant_scope):
    saver = assistant_scope.saver
    for wrong in (
        {"configurable": {"thread_id": str(uuid4())}},
        {"configurable": {"thread_id": str(assistant_scope.turn), "checkpoint_ns": "other"}},
        {"configurable": {"thread_id": str(assistant_scope.turn), "checkpoint_id": "x" * 129}},
    ):
        with pytest.raises(ValueError):
            await saver.aget_tuple(wrong)
        with pytest.raises(ValueError):
            await saver.aput(wrong, empty_checkpoint(), {}, {})
    with pytest.raises(ValueError):
        await saver.adelete_thread(str(uuid4()))


async def test_checkpoint_owner_and_tenant_rls_default_deny(assistant_scope, identities):
    scope = assistant_scope
    await scope.saver.aput(scope.saver.config(), empty_checkpoint(), {"step": 0}, {})
    for org, owner in (
        (scope.context.organization_id, scope.other_user),
        (identities[1]["org"], identities[1]["user"]),
    ):
        other = PostgreSQLCheckpointSaver(
            scope.sessions,
            RuntimeContext(org, owner, frozenset({"ai.use"}), "other-user", "AI"),
            scope.conversation,
            scope.turn,
        )
        assert await other.aget_tuple(other.config()) is None
        assert [item async for item in other.alist(None)] == []
        with pytest.raises(Problem) as exc:
            await other.aput(other.config(), empty_checkpoint(), {}, {})
        assert exc.value.status == 404
        await other.adelete_thread(str(scope.turn))
    assert await scope.saver.aget_tuple(scope.saver.config()) is not None
    async with scope.sessions.begin() as db:
        # Missing user (even with the correct tenant) cannot access private state.
        await db.execute(
            text("SELECT set_config('app.organization_id',:org,true)"),
            {"org": str(scope.context.organization_id)},
        )
        for table in AI_TABLES:
            assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar_one() == 0
    async with scope.sessions.begin() as db:
        # Attempt the write directly so RLS WITH CHECK itself is exercised.
        await db.execute(
            text(
                "SELECT set_config('app.organization_id',:org,true),"
                "set_config('app.user_id',:owner,true)"
            ),
            {"org": str(scope.context.organization_id), "owner": str(scope.other_user)},
        )
        with pytest.raises(DBAPIError):
            await db.execute(
                text(
                    "INSERT INTO forge.assistant_conversations "
                    "(organization_id,owner_id,permissions_hash) VALUES (:org,:owner,:hash)"
                ),
                {
                    "org": scope.context.organization_id,
                    "owner": scope.context.user_id,
                    "hash": "c" * 64,
                },
            )


@pytest.mark.parametrize("condition", ["deleted", "conversation_expired", "turn_expired"])
async def test_checkpoint_deleted_or_expired_state_is_unreadable_and_cannot_resume(
    assistant_scope, condition
):
    scope = assistant_scope
    await scope.saver.aput(scope.saver.config(), empty_checkpoint(), {}, {})
    async with scope.sessions.begin() as db:
        await db.execute(
            text(
                "SELECT set_config('app.organization_id',:org,true),"
                "set_config('app.user_id',:owner,true)"
            ),
            {"org": str(scope.context.organization_id), "owner": str(scope.context.user_id)},
        )
        if condition == "deleted":
            statement = (
                "UPDATE forge.assistant_conversations SET deleted_at=clock_timestamp() WHERE id=:id"
            )
            row_id = scope.conversation
        elif condition == "conversation_expired":
            statement = (
                "UPDATE forge.assistant_conversations "
                "SET expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"
            )
            row_id = scope.conversation
        else:
            statement = (
                "UPDATE forge.assistant_turns "
                "SET expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"
            )
            row_id = scope.turn
        await db.execute(text(statement), {"id": row_id})
    assert await scope.saver.aget_tuple(scope.saver.config()) is None
    assert [item async for item in scope.saver.alist(None)] == []
    with pytest.raises(Problem):
        await scope.saver.aput(scope.saver.config(), empty_checkpoint(), {}, {})
    # Explicit cleanup is allowed for expired state, scoped to this bound turn.
    await scope.saver.adelete_thread(str(scope.turn))


async def test_assistant_tables_force_rls_and_receipts_are_insert_only(assistant_scope):
    async with assistant_scope.sessions.begin() as db:
        tables = (
            await db.execute(
                text(
                    "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class "
                    "JOIN pg_namespace n ON n.oid=relnamespace WHERE n.nspname='forge' "
                    "AND relname=ANY(:names)"
                ),
                {"names": list(AI_TABLES)},
            )
        ).all()
        assert len(tables) == 6
        assert all(row[1] and row[2] for row in tables)
        grants = (
            await db.execute(
                text(
                    "SELECT "
                    "has_table_privilege('forge_app','forge.assistant_draft_receipts','SELECT'),"
                    "has_table_privilege('forge_app','forge.assistant_draft_receipts','INSERT'),"
                    "has_table_privilege('forge_app','forge.assistant_draft_receipts','UPDATE'),"
                    "has_table_privilege('forge_app','forge.assistant_draft_receipts','DELETE')"
                )
            )
        ).one()
        assert tuple(grants) == (True, True, False, False)


async def test_checkpoint_concurrent_savers_share_persistent_count_limit(
    assistant_scope, monkeypatch
):
    from forge_erp.modules.assistant.infrastructure import checkpointer

    monkeypatch.setattr(checkpointer, "_MAX_CHECKPOINTS", 2)
    scope = assistant_scope
    savers = [
        PostgreSQLCheckpointSaver(scope.sessions, scope.context, scope.conversation, scope.turn)
        for _ in range(8)
    ]
    results = await asyncio.gather(
        *(saver.aput(saver.config(), empty_checkpoint(), {}, {}) for saver in savers),
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) for result in results) == 2
    assert sum(isinstance(result, ValueError) for result in results) == 6
    assert len([item async for item in scope.saver.alist(None)]) == 2


async def test_checkpoint_write_limit_preserves_idempotent_retry(assistant_scope, monkeypatch):
    from forge_erp.modules.assistant.infrastructure import checkpointer

    monkeypatch.setattr(checkpointer, "_MAX_WRITES", 1)
    saver = assistant_scope.saver
    config = await saver.aput(saver.config(), empty_checkpoint(), {}, {})
    await saver.aput_writes(config, [("result", "first")], "task-1")
    await saver.aput_writes(config, [("result", "retry")], "task-1")
    with pytest.raises(ValueError, match="count limit"):
        await saver.aput_writes(config, [("result", "excessive")], "task-2")
    saved = await saver.aget_tuple(config)
    assert saved.pending_writes == [("task-1", "result", "first")]


async def test_checkpoint_old_attempt_cannot_read_write_or_delete_new_worker_state(assistant_scope):
    scope = assistant_scope
    stale = PostgreSQLCheckpointSaver(
        scope.sessions, scope.context, scope.conversation, scope.turn, attempt=0
    )
    config = await stale.aput(stale.config(), empty_checkpoint(), {}, {})
    async with scope.sessions.begin() as db:
        await db.execute(
            text(
                "SELECT set_config('app.organization_id',:org,true),"
                "set_config('app.user_id',:owner,true)"
            ),
            {"org": str(scope.context.organization_id), "owner": str(scope.context.user_id)},
        )
        await db.execute(
            text(
                "UPDATE forge.assistant_turns SET attempts=1,state='WAITING',"
                "lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"
            ),
            {"id": scope.turn},
        )
    assert await stale.aget_tuple(stale.config()) is None
    assert [row async for row in stale.alist(None)] == []
    with pytest.raises(Problem):
        await stale.aput(stale.config(), empty_checkpoint(), {}, {})
    with pytest.raises(Problem):
        await stale.aput_writes(config, [("result", "stale")], "stale-task")
    with pytest.raises(Problem):
        await stale.adelete_thread(str(scope.turn))
    # A reviewed WAITING turn may resume with the same current attempt after its
    # old running lease expires; the proposal policy is checked by application.
    current = PostgreSQLCheckpointSaver(
        scope.sessions, scope.context, scope.conversation, scope.turn, attempt=1
    )
    assert await current.aget_tuple(current.config()) is not None
    await current.aput_writes(config, [("result", "current")], "current-task")
    saved = await current.aget_tuple(current.config())
    assert saved.pending_writes == [("current-task", "result", "current")]


async def test_checkpoint_waiting_on_parent_does_not_lock_child_ahead_of_retention(
    assistant_scope, monkeypatch
):
    scope = assistant_scope
    saver = scope.saver
    ready = asyncio.Event()
    writer_pid = []
    original_lock = saver._lock_turn

    async def observed_lock(db):
        await original_lock(db)
        writer_pid.append((await db.execute(text("SELECT pg_backend_pid()"))).scalar_one())
        ready.set()

    monkeypatch.setattr(saver, "_lock_turn", observed_lock)
    writer = None
    try:
        async with scope.sessions.begin() as holder:
            await holder.execute(
                text(
                    "SELECT set_config('app.organization_id',:org,true),"
                    "set_config('app.user_id',:owner,true)"
                ),
                {"org": str(scope.context.organization_id), "owner": str(scope.context.user_id)},
            )
            await holder.execute(text("SELECT 1 FROM forge.assistant_conversations FOR UPDATE"))
            writer = asyncio.create_task(saver.aput(saver.config(), empty_checkpoint(), {}, {}))
            await asyncio.wait_for(ready.wait(), timeout=3)

            async def wait_until_database_reports_lock():
                while True:
                    async with scope.sessions.begin() as observer:
                        waiting = (
                            await observer.execute(
                                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),
                                {"pid": writer_pid[0]},
                            )
                        ).scalar_one()
                    if waiting == "Lock":
                        return
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_until_database_reports_lock(), timeout=3)
            # A joined child-first lock would hold this row while waiting for our
            # parent, creating a cycle with retention/review's parent-first order.
            await holder.execute(text("SELECT 1 FROM forge.assistant_turns FOR UPDATE NOWAIT"))
    finally:
        if writer is not None:
            await asyncio.wait_for(writer, timeout=5)
