"""Recover a real, deliberately exited test process using only committed PostgreSQL state."""

import asyncio
import json
import os
import subprocess
import sys
from uuid import UUID, uuid4

from sqlalchemy import text
from test_assistant_runtime import assistant as assistant

from forge_erp.core.db import sessions, set_tenant
from forge_erp.modules.assistant.application import state

# All credentials are synthetic fixture values passed over stdin. Neither subprocess
# output nor its environment is included in assertions or pytest failure messages.
CHILD = r"""
import asyncio
import json
import os
import sys
from uuid import UUID

from langchain_core.runnables import RunnableLambda
from sqlalchemy import text

from forge_erp.core.db import engine, sessions
from forge_erp.modules.assistant.application import runtime, state
from forge_erp.modules.assistant.domain.checkpoint import CheckpointJSONCodec

data = json.loads(sys.stdin.read())
calls = 0

async def persisted_query():
    codec = CheckpointJSONCodec()
    for _ in range(200):
        async with sessions.begin() as db:
            ctx = await state.authorize(db, data['token'], 'process-checkpoint-probe',
                                        UUID(data['conversation_id']))
            rows = (await db.execute(text(
                'SELECT checkpoint FROM forge.assistant_checkpoints '
                'WHERE organization_id=:org AND owner_id=:owner '
                'AND conversation_id=:conversation'
            ), {'org': ctx.organization_id, 'owner': ctx.user_id,
                'conversation': UUID(data['conversation_id'])})).scalars().all()
            found = False
            for row in rows:
                checkpoint = codec.loads_typed(('forge-json-v1', json.dumps(row).encode()))
                values = checkpoint.get('channel_values', {})
                if (values.get('route') == 'decide'
                    and len(values.get('results', [])) == 1
                    and values['results'][0].get('evidence_id') == 'e1'):
                    found = True
        # The independent probe transaction is already closed before abrupt exit.
        if found:
            return True
        await asyncio.sleep(0.01)
    return False

async def model(messages, **kwargs):
    global calls
    calls += 1
    context = json.loads(messages[-1].content)
    if data['phase'] == 'crash':
        if calls == 1:
            return {'action': 'query', 'tool': 'get_inventory', 'arguments': {}}
        if calls != 2 or not await persisted_query():
            os._exit(87)
        os._exit(86)
    if (calls != 1 or len(context.get('query_results', [])) != 1
        or context['query_results'][0].get('evidence_id') != 'e1'):
        os._exit(88)
    return {'action': 'answer', 'code': 'results', 'evidence_ids': ['e1']}

async def main():
    async with sessions.begin() as db:
        ctx = await state.authorize(db, data['token'], 'process-role-check',
                                    UUID(data['conversation_id']))
        code = (await db.execute(text('SELECT code FROM forge.organizations '
                                     'WHERE id=:org'), {'org': ctx.organization_id})).scalar_one()
        role = (await db.execute(text('SELECT current_user'))).scalar_one()
        if not code.startswith('TEST_') or role != 'forge_app':
            return 89
    runtime.decision_runnable = RunnableLambda(model)
    if data['phase'] == 'crash':
        await runtime.run_message(data['token'], 'process-before-exit',
            UUID(data['conversation_id']), {'message': '查询当前库存'}, data['key'])
        return 90
    result = await runtime.retry_turn(data['token'], 'process-after-exit',
                                     UUID(data['turn_id']), data['retry_key'])
    expected = (str(result.id) == data['turn_id'] and result.state == 'COMPLETED'
                and result.model_calls == 3 and result.tool_calls == 1)
    await engine.dispose()
    return 0 if expected else 91

sys.exit(asyncio.run(main()))
"""


async def child(payload: dict) -> int:
    def invoke() -> int:
        try:
            completed = subprocess.run(
                [sys.executable, "-c", CHILD],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=60,
                env=os.environ
                | {
                    "AI_TRACE_ENABLED": "false",
                    "LANGCHAIN_TRACING_V2": "false",
                    "LANGSMITH_TRACING": "false",
                },
            )
        except subprocess.TimeoutExpired:
            return 124
        return completed.returncode

    return await asyncio.to_thread(invoke)


async def test_terminated_process_recovers_same_turn_from_postgres_without_repeating_tool(
    assistant, identities
):
    client, identity, conversation = assistant
    assert identity["code"].startswith("TEST_")
    payload = {
        "token": client.cookies["forge_session"],
        "conversation_id": conversation,
        "key": uuid4().hex,
        "phase": "crash",
    }
    assert await child(payload) == 86
    async with sessions.begin() as db:
        ctx = await state.authorize(db, payload["token"], "process-parent", UUID(conversation))
        raw = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM forge.assistant_turns WHERE organization_id=:org "
                        "AND owner_id=:owner AND conversation_id=:conversation"
                    ),
                    {
                        "org": ctx.organization_id,
                        "owner": ctx.user_id,
                        "conversation": conversation,
                    },
                )
            )
            .mappings()
            .one()
        )
        assert raw["state"] == "RUNNING" and raw["attempts"] == 1
        assert raw["model_calls"] == 2 and raw["tool_calls"] == 1
        turn_id = raw["id"]
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.assistant_checkpoints WHERE turn_id=:id"),
                {"id": turn_id},
            )
        ).scalar_one() > 0
        # Only this TEST_* turn's clock is advanced; no service or other process is stopped.
        await db.execute(
            text(
                "UPDATE forge.assistant_turns "
                "SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE organization_id=:org AND owner_id=:owner AND id=:id"
            ),
            {"org": ctx.organization_id, "owner": ctx.user_id, "id": turn_id},
        )
    for org, owner in (
        (identity["org"], uuid4()),
        (identities[1]["org"], identities[1]["user"]),
    ):
        async with sessions.begin() as db:
            await set_tenant(db, org)
            await db.execute(
                text("SELECT set_config('app.user_id',:owner,true)"), {"owner": str(owner)}
            )
            for table in (
                "assistant_turns",
                "assistant_checkpoints",
                "assistant_checkpoint_writes",
            ):
                assert (
                    await db.execute(text(f"SELECT count(*) FROM forge.{table}"))
                ).scalar_one() == 0
    resumed = payload | {"phase": "resume", "turn_id": str(turn_id), "retry_key": uuid4().hex}
    assert await child(resumed) == 0
    async with sessions.begin() as db:
        ctx = await state.authorize(db, payload["token"], "process-final", UUID(conversation))
        result = await state.read_turn(db, ctx, turn_id)
        assert result.id == turn_id and result.state == "COMPLETED"
        assert result.model_calls == 3 and result.tool_calls == 1
        assert len(result.evidence) == 1 and result.evidence[0].id == "e1"
        assert result.proposal is None
        assert (
            await db.execute(text("SELECT count(*) FROM forge.assistant_turns"))
        ).scalar_one() == 1
        assert (
            await db.execute(
                text("SELECT attempts FROM forge.assistant_turns WHERE id=:id"), {"id": turn_id}
            )
        ).scalar_one() == 2
        for table in (
            "sales_orders",
            "purchase_orders",
            "inventory_documents",
            "inventory_movements",
            "inventory_balances",
            "inventory_reservations",
            "funds_entries",
            "funds_cash_documents",
            "assistant_draft_receipts",
        ):
            assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar_one() == 0
