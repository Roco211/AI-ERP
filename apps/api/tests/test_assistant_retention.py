"""Expired AI bodies disappear; owner boundaries and business evidence remain."""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from sqlalchemy import create_engine, text
from test_assistant_checkpoint import assistant_scope as assistant_scope
from test_catalog import catalog_client as catalog_client
from test_sales_orders import detail, so
from test_sales_orders import sale as sale

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions
from forge_erp.core.errors import Problem
from forge_erp.modules.assistant.application import retention
from forge_erp.modules.assistant.infrastructure.checkpointer import PostgreSQLCheckpointSaver

PRIVATE_BODY = "private-retention-test-body-must-disappear"


@asynccontextmanager
async def owned(scope):
    async with scope.sessions.begin() as db:
        await db.execute(
            text(
                "SELECT set_config('app.organization_id',:org,true),"
                "set_config('app.user_id',:owner,true)"
            ),
            {"org": str(scope.context.organization_id), "owner": str(scope.context.user_id)},
        )
        yield db


def system(scope):
    return replace(scope.context, source="SYSTEM", permissions=frozenset())


async def fill(scope, state="WAITING", proposal_status="PENDING"):
    proposal = uuid4()
    async with owned(scope) as db:
        await db.execute(
            text("UPDATE forge.assistant_conversations SET title=:body WHERE id=:id"),
            {"body": PRIVATE_BODY, "id": scope.conversation},
        )
        await db.execute(
            text(
                "UPDATE forge.assistant_turns SET prompt=:body,request_body=CAST(:json AS jsonb),"
                "response=CAST(:json AS "
                "jsonb),state=:state,lease_until=clock_timestamp()+interval '1 hour' "
                "WHERE id=:id"
            ),
            {
                "body": PRIVATE_BODY,
                "json": json.dumps({"text": PRIVATE_BODY}),
                "state": state,
                "id": scope.turn,
            },
        )
        await db.execute(
            text(
                "INSERT INTO forge.assistant_proposals "
                "(id,organization_id,owner_id,conversation_id,turn_id,kind,body,preview,"
                "confirmation_hash,status) VALUES (:id,:org,:owner,:conversation,:turn,'SALES',"
                "CAST(:body AS jsonb),CAST(:body AS jsonb),:hash,:status)"
            ),
            {
                "id": proposal,
                "org": scope.context.organization_id,
                "owner": scope.context.user_id,
                "conversation": scope.conversation,
                "turn": scope.turn,
                "body": json.dumps({"text": PRIVATE_BODY}),
                "hash": "a" * 64,
                "status": proposal_status,
            },
        )
    saver = PostgreSQLCheckpointSaver(scope.sessions, scope.context, scope.conversation, scope.turn)
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"body": PRIVATE_BODY}
    config = await saver.aput(saver.config(), checkpoint, {"step": 0}, {})
    await saver.aput_writes(config, [("body", PRIVATE_BODY)], "private-write")
    return proposal


async def expire(scope, mode="conversation"):
    async with owned(scope) as db:
        if mode == "deleted":
            await db.execute(
                text(
                    "UPDATE forge.assistant_conversations SET deleted_at=clock_timestamp() WHERE "
                    "id=:id"
                ),
                {"id": scope.conversation},
            )
        elif mode == "turn":
            await db.execute(
                text(
                    "UPDATE forge.assistant_turns SET expires_at=clock_timestamp()-interval '1 "
                    "second' WHERE id=:id"
                ),
                {"id": scope.turn},
            )
        else:
            await db.execute(
                text(
                    "UPDATE forge.assistant_conversations SET "
                    "expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"
                ),
                {"id": scope.conversation},
            )


async def snapshot(scope):
    async with owned(scope) as db:
        return {
            table: [
                dict(row)
                for row in (await db.execute(text(f"SELECT * FROM forge.{table}"))).mappings()
            ]
            for table in (
                "assistant_conversations",
                "assistant_turns",
                "assistant_proposals",
                "assistant_checkpoints",
                "assistant_checkpoint_writes",
                "assistant_draft_receipts",
            )
        }


@pytest.mark.parametrize("mode", ["conversation", "turn", "deleted"])
async def test_retention_erases_all_private_body_locations_with_system_audit(assistant_scope, mode):
    scope = assistant_scope
    await fill(scope)
    await expire(scope, mode)
    result = await retention.drain(scope.sessions, scope.context.organization_id)
    assert result == {"conversations": int(mode != "turn"), "turns": 1, "proposals": 1}
    after = await snapshot(scope)
    turn = after["assistant_turns"][0]
    assert turn["prompt"] == "" and turn["request_body"] == {} and turn["response"] is None
    assert turn["state"] == "FAILED" and turn["error_code"] == "AI_STATE_EXPIRED"
    assert turn["body_purged_at"] and turn["lease_until"] is None
    proposal = after["assistant_proposals"][0]
    assert proposal["body"] == {} and proposal["preview"] == {}
    assert proposal["status"] == "EXPIRED" and proposal["confirmation_hash"] == "a" * 64
    assert after["assistant_checkpoints"] == after["assistant_checkpoint_writes"] == []
    if mode != "turn":
        assert PRIVATE_BODY not in json.dumps(after, default=str)
        assert after["assistant_conversations"][0]["body_purged_at"]
    else:
        assert after["assistant_conversations"][0]["body_purged_at"] is None
    async with owned(scope) as db:
        audit = (
            await db.execute(
                text(
                    "SELECT actor_type,actor_id,source,after FROM forge.audit_events WHERE "
                    "action='ai.retention.purged'"
                )
            )
        ).one()
        assert tuple(audit[:3]) == ("SYSTEM", None, "SYSTEM")
        assert PRIVATE_BODY not in json.dumps(audit.after)
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.outbox_events WHERE "
                    "event_type='ai.retention.purged'"
                )
            )
        ).scalar_one() == 1
    assert await retention.drain(scope.sessions, scope.context.organization_id) == {
        "conversations": 0,
        "turns": 0,
        "proposals": 0,
    }


async def test_retention_does_not_erase_live_conversation_or_short_lived_proposal(assistant_scope):
    scope = assistant_scope
    await fill(scope)
    async with owned(scope) as db:
        await db.execute(
            text(
                "UPDATE forge.assistant_proposals SET expires_at=clock_timestamp()-interval '1 "
                "minute'"
            )
        )
    before = await snapshot(scope)
    assert await retention.drain(scope.sessions, scope.context.organization_id) == {
        "conversations": 0,
        "turns": 0,
        "proposals": 0,
    }
    assert await snapshot(scope) == before


async def test_retention_preserves_created_order_receipt_and_completed_metadata(
    assistant_scope, catalog_client, sale
):
    scope = assistant_scope
    order = await so(catalog_client, sale)
    proposal = await fill(scope, state="COMPLETED", proposal_status="CREATED")
    async with owned(scope) as db:
        await db.execute(
            text(
                "INSERT INTO forge.assistant_draft_receipts "
                "(organization_id,owner_id,conversation_id,turn_id,proposal_id,revision,"
                "confirmation_hash,kind,sales_order_id,receipt,request_id) VALUES "
                "(:org,:owner,:conversation,:turn,:proposal,1,:hash,'SALES',:order,"
                "CAST(:receipt AS jsonb),'permanent-test-receipt')"
            ),
            {
                "org": scope.context.organization_id,
                "owner": scope.context.user_id,
                "conversation": scope.conversation,
                "turn": scope.turn,
                "proposal": proposal,
                "hash": "a" * 64,
                "order": UUID(order["id"]),
                "receipt": json.dumps({"id": order["id"], "status": "DRAFT"}),
            },
        )
    before = await snapshot(scope)
    original_order = await detail(catalog_client, order)
    await expire(scope)
    await retention.drain(scope.sessions, scope.context.organization_id)
    after = await snapshot(scope)
    assert after["assistant_draft_receipts"] == before["assistant_draft_receipts"]
    assert after["assistant_turns"][0]["state"] == "COMPLETED"
    assert (
        after["assistant_turns"][0]["request_hash"] == before["assistant_turns"][0]["request_hash"]
    )
    assert after["assistant_proposals"][0]["status"] == "CREATED"
    assert await detail(catalog_client, order) == original_order
    async with owned(scope) as db:
        for table in (
            "inventory_movements",
            "inventory_reservations",
            "funds_entries",
            "funds_cash_documents",
        ):
            assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar_one() == 0


async def test_retention_scope_cannot_purge_another_owner_or_tenant(assistant_scope, identities):
    scope = assistant_scope
    await fill(scope)
    await expire(scope)
    before = await snapshot(scope)
    for org, owner in (
        (scope.context.organization_id, scope.other_user),
        (identities[1]["org"], identities[1]["user"]),
    ):
        result = await retention.purge_scope(
            scope.sessions,
            RuntimeContext(org, owner, frozenset(), "system-test", "SYSTEM"),
            scope.conversation,
        )
        assert result == {"conversations": 0, "turns": 0, "proposals": 0}
    assert await snapshot(scope) == before
    with pytest.raises(Problem):
        await retention.purge_scope(scope.sessions, scope.context, scope.conversation)
    assert await retention.drain(scope.sessions, identities[1]["org"]) == {
        "conversations": 0,
        "turns": 0,
        "proposals": 0,
    }


async def test_retention_runs_for_disabled_owner_and_organization(assistant_scope):
    scope = assistant_scope
    await fill(scope)
    await expire(scope)
    admin = create_engine(settings().migration_database_url)
    try:
        with admin.begin() as db:
            db.execute(
                text("UPDATE forge.organizations SET active=false WHERE id=:id"),
                {"id": scope.context.organization_id},
            )
            db.execute(
                text("UPDATE forge.users SET active=false WHERE id=:id"),
                {"id": scope.context.user_id},
            )
        assert (await retention.drain(scope.sessions, scope.context.organization_id))["turns"] == 1
    finally:
        admin.dispose()


async def add_turn(scope):
    turn = uuid4()
    async with owned(scope) as db:
        await db.execute(
            text(
                "INSERT INTO forge.assistant_turns(id,organization_id,owner_id,conversation_id,"
                "idempotency_key,request_hash,prompt,request_id) "
                "VALUES(:id,:org,:owner,:conversation,:key,:hash,:body,'retention-extra')"
            ),
            {
                "id": turn,
                "org": scope.context.organization_id,
                "owner": scope.context.user_id,
                "conversation": scope.conversation,
                "key": uuid4().hex,
                "hash": "c" * 64,
                "body": PRIVATE_BODY,
            },
        )
    return SimpleNamespace(**{**vars(scope), "turn": turn})


async def test_retention_batches_are_bounded_and_continue_without_erasing_newer_work(
    assistant_scope,
):
    scope = assistant_scope
    await fill(scope)
    for _ in range(4):
        extra = await add_turn(scope)
        await fill(extra)
    await expire(scope)
    first = await retention.drain(scope.sessions, scope.context.organization_id, limit=2)
    assert first == {"conversations": 0, "turns": 2, "proposals": 2}
    after = await snapshot(scope)
    assert sum(row["body_purged_at"] is not None for row in after["assistant_turns"]) == 2
    assert after["assistant_conversations"][0]["body_purged_at"] is None
    second = await retention.drain(scope.sessions, scope.context.organization_id, limit=2)
    third = await retention.drain(scope.sessions, scope.context.organization_id, limit=2)
    assert second["turns"] == 2 and third == {"conversations": 1, "turns": 1, "proposals": 1}


async def test_retention_concurrent_workers_and_locked_parent_are_safe(assistant_scope):
    scope = assistant_scope
    await fill(scope)
    await expire(scope)
    async with owned(scope) as db:
        await db.execute(text("SELECT 1 FROM forge.assistant_conversations FOR UPDATE"))
        result = await asyncio.wait_for(
            retention.purge_scope(scope.sessions, system(scope), scope.conversation), timeout=2
        )
        assert not any(result.values())
    results = await asyncio.gather(
        *(retention.drain(scope.sessions, scope.context.organization_id) for _ in range(4))
    )
    assert sum(row["turns"] for row in results) == 1
    assert sum(row["conversations"] for row in results) == 1
    async with owned(scope) as db:
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.audit_events WHERE action='ai.retention.purged'")
            )
        ).scalar_one() == 1


async def test_retention_audit_failure_rolls_back_erasure_and_markers(assistant_scope, monkeypatch):
    scope = assistant_scope
    await fill(scope)
    await expire(scope)
    before = await snapshot(scope)

    async def fail(*args):
        raise Problem(503, "TEST_AUDIT_FAILURE", "synthetic audit failure")

    monkeypatch.setattr(retention, "_record", fail)
    with pytest.raises(Problem):
        await retention.drain(scope.sessions, scope.context.organization_id)
    assert await snapshot(scope) == before


async def test_retention_discovery_exposes_only_bounded_ids_and_revokes_public(assistant_scope):
    scope = assistant_scope
    async with owned(scope) as db:
        await db.execute(
            text(
                "INSERT INTO "
                "forge.assistant_conversations(organization_id,owner_id,permissions_hash,"
                "title,expires_at) SELECT :org,:owner,:hash,:title,clock_timestamp()-interval '1 "
                "day' "
                "FROM generate_series(1,105)"
            ),
            {
                "org": scope.context.organization_id,
                "owner": scope.context.user_id,
                "hash": "e" * 64,
                "title": PRIVATE_BODY,
            },
        )
    async with sessions.begin() as db:
        result = (
            (await db.execute(text("SELECT * FROM forge.pending_assistant_retention(100000,NULL)")))
            .mappings()
            .all()
        )
        assert len(result) == 100
        assert all(set(row) == {"organization_id", "owner_id", "conversation_id"} for row in result)
        assert PRIVATE_BODY not in str(result)
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.pending_assistant_retention(-1,NULL)")
            )
        ).scalar_one() == 0
        privileges = (
            await db.execute(
                text(
                    "SELECT p.prosecdef,p.proconfig,NOT EXISTS(SELECT 1 FROM "
                    "aclexplode(p.proacl) a "
                    "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS private "
                    "FROM pg_proc p WHERE "
                    "p.oid='forge.pending_assistant_retention(integer,uuid)'::regprocedure"
                )
            )
        ).one()
        assert privileges.prosecdef and privileges.private
        assert privileges.proconfig == ["search_path=pg_catalog"]


@pytest.mark.parametrize("limit", [0, -1, 101, True])
async def test_retention_limit_cannot_be_raised_or_disabled(assistant_scope, limit):
    with pytest.raises(ValueError):
        await retention.drain(assistant_scope.sessions, limit=limit)


async def test_retention_worker_entrypoint_uses_ordinary_application_role(assistant_scope):
    from forge_erp.workers.assistant_retention import run

    scope = assistant_scope
    await fill(scope)
    await expire(scope)
    assert await run(scope.context.organization_id, limit=1) == {
        "conversations": 1,
        "turns": 1,
        "proposals": 1,
    }


async def test_retention_rejects_migration_role_even_for_discovery():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    migration_engine = create_async_engine(settings().migration_database_url)
    try:
        with pytest.raises(RuntimeError, match="restricted forge_app"):
            await retention.drain(async_sessionmaker(migration_engine))
    finally:
        await migration_engine.dispose()
