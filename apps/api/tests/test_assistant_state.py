import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from test_catalog import catalog_client as catalog_client
from test_sales_orders import sale as sale

from forge_erp.core.config import settings
from forge_erp.core.db import sessions
from forge_erp.core.errors import Problem
from forge_erp.modules.assistant.application import state
from forge_erp.modules.assistant.domain.chat import (
    ConversationInput,
    ProposalApproval,
    ProposalEdit,
    ProposalRejection,
)
from forge_erp.modules.assistant.domain.drafts import DRAFT_INPUT


@asynccontextmanager
async def tx(client):
    async with sessions.begin() as db:
        ctx = await state.authorize(db, client.cookies["forge_session"], "state-test")
        yield db, ctx


async def conversation(client):
    async with tx(client) as pair:
        return await state.create_conversation(
            *pair, ConversationInput(title="测试助手"), uuid4().hex
        )


async def claim(client, id, *, key=None, message="库存情况", kind="CHAT", request=None):
    async with tx(client) as pair:
        return await state.claim_turn(
            *pair, id, request or {"message": message}, key or uuid4().hex, kind
        )


async def proposed(client, sale):
    conv = await conversation(client)
    turn, _ = await claim(client, conv.id)
    async with tx(client) as pair:
        proposal = await state.create_proposal(
            *pair,
            turn["id"],
            turn["attempts"],
            DRAFT_INPUT.validate_python({"kind": "SALES", "order": sale}),
        )
        await state.finish_turn(
            *pair,
            conv.id,
            turn["id"],
            turn["attempts"],
            {"answer": "请复核草稿", "evidence": []},
            "WAITING",
        )
    return conv, turn, proposal


def approved(proposal):
    return ProposalApproval(
        expected_revision=proposal.revision, confirmation_hash=proposal.preview.confirmation_hash
    )


def admin_execute(sql, params):
    engine = create_engine(settings().migration_database_url)
    try:
        with engine.begin() as db:
            db.execute(text(sql), params)
    finally:
        engine.dispose()


async def test_conversation_creation_idempotency_and_private_history(catalog_client):
    key = uuid4().hex
    async with tx(catalog_client) as pair:
        first = await state.create_conversation(*pair, ConversationInput(title="库存"), key)
    async with tx(catalog_client) as pair:
        again = await state.create_conversation(*pair, ConversationInput(title="库存"), key)
        assert first.id == again.id
        page = await state.list_conversations(*pair)
        assert page.total == 1 and page.items[0].id == first.id
        assert (await state.conversation_detail(*pair, first.id)).turns == []
        with pytest.raises(Problem) as exc:
            await state.create_conversation(*pair, ConversationInput(title="修改输入"), key)
        assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"


async def test_same_organization_other_user_and_rls_deny_history(
    catalog_client, identities, password_hash
):
    conv = await conversation(catalog_client)
    ident = identities[0]
    other = uuid4()
    admin_execute(
        "INSERT INTO forge.users(id,organization_id,email,display_name,password_hash) "
        "VALUES(:id,:org,'state-other@example.test','Other',:hash)",
        {"id": other, "org": ident["org"], "hash": password_hash},
    )
    admin_execute(
        "INSERT INTO forge.user_roles(organization_id,user_id,role_id) VALUES(:org,:user,:role)",
        {"org": ident["org"], "user": other, "role": ident["role"]},
    )
    async with tx(catalog_client) as (db, ctx):
        with pytest.raises(Problem) as exc:
            await state.require_conversation(db, replace(ctx, user_id=other), conv.id)
        assert exc.value.status == 404
        assert (
            await db.execute(text("SELECT count(*) FROM forge.assistant_conversations"))
        ).scalar_one() == 0
    async with sessions.begin() as db:
        assert (
            await db.execute(text("SELECT count(*) FROM forge.assistant_conversations"))
        ).scalar_one() == 0


@pytest.mark.parametrize("change", ["permissions", "provider"])
async def test_permission_or_provider_changes_block_history_and_resume(
    catalog_client, identities, change
):
    conv = await conversation(catalog_client)
    turn, _ = await claim(catalog_client, conv.id)
    ident = identities[0]
    if change == "permissions":
        admin_execute(
            "DELETE FROM forge.role_permissions WHERE organization_id=:org AND role_id=:role "
            "AND permission_code='product.cost.read'",
            ident,
        )
    else:
        admin_execute(
            "INSERT INTO forge.ai_provider_settings "
            "(organization_id,name,base_url,model,updated_by) VALUES(:org,'Other','https://example.test/v1','test',:user)",
            ident,
        )
    async with tx(catalog_client) as pair:
        assert (await state.list_conversations(*pair)).total == 0
        for operation in (
            lambda: state.conversation_detail(*pair, conv.id),
            lambda: state.read_turn(*pair, turn["id"]),
            lambda: state.claim_turn(*pair, conv.id, {"message": "继续"}, uuid4().hex),
        ):
            with pytest.raises(Problem) as exc:
                await operation()
            assert exc.value.code == "AI_CONTEXT_CHANGED"
        assert (await state.delete_conversation(*pair, conv.id, uuid4().hex)).deleted


@pytest.mark.parametrize("change", ["logout", "user_disabled", "organization_disabled", "expired"])
async def test_existing_ai_conversation_requires_fresh_active_session(
    catalog_client, identities, change
):
    conv = await conversation(catalog_client)
    await claim(catalog_client, conv.id)
    token = catalog_client.cookies["forge_session"]
    if change == "logout":
        assert (await catalog_client.post("/api/v1/auth/logout")).status_code == 204
    else:
        sql = {
            "user_disabled": "UPDATE forge.users SET active=false WHERE organization_id=:org",
            "organization_disabled": "UPDATE forge.organizations SET active=false WHERE id=:org",
            "expired": "UPDATE forge.sessions SET expires_at=now()-interval '1 second' "
            "WHERE organization_id=:org",
        }[change]
        admin_execute(sql, identities[0])
    async with sessions.begin() as db:
        with pytest.raises(Problem) as exc:
            await state.authorize(db, token, "continuation-after-auth-change", conv.id)
        assert exc.value.status == 401


async def test_turn_keys_conversation_lease_and_retry_attempt_fences(catalog_client):
    conv = await conversation(catalog_client)
    key = uuid4().hex
    first, run = await claim(catalog_client, conv.id, key=key)
    assert run and first["attempts"] == 1
    same, run = await claim(catalog_client, conv.id, key=key)
    assert same["id"] == first["id"] and not run
    with pytest.raises(Problem) as exc:
        await claim(catalog_client, conv.id)
    assert exc.value.code == "AI_CONVERSATION_BUSY"
    with pytest.raises(Problem) as exc:
        await claim(catalog_client, conv.id, key=key, message="不同问题")
    assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"
    admin_execute(
        "UPDATE forge.assistant_turns SET lease_until=now()-interval '1 second' WHERE id=:id", first
    )
    again, run = await claim(catalog_client, conv.id, key=key)
    assert run and again["attempts"] == 2
    async with tx(catalog_client) as pair:
        with pytest.raises(Problem) as exc:
            await state.consume_budget(*pair, conv.id, first["id"], 1, "model")
        assert exc.value.code == "AI_TURN_FENCED"
        result = await state.finish_turn(
            *pair,
            conv.id,
            first["id"],
            2,
            {"answer": "失败", "evidence": []},
            "FAILED",
            "AI_PROVIDER_TIMEOUT",
        )
        assert not result.can_retry
    _, run = await claim(catalog_client, conv.id, key=key)
    assert not run


async def test_concurrent_claims_only_start_one_run(catalog_client):
    conv = await conversation(catalog_client)

    async def attempt():
        try:
            return (await claim(catalog_client, conv.id))[1]
        except Problem as exc:
            return exc.code

    assert sorted(await asyncio.gather(attempt(), attempt()), key=str) == [
        "AI_CONVERSATION_BUSY",
        True,
    ]


@pytest.mark.parametrize("kind,limit", [("model", 5), ("tool", 8)])
async def test_cumulative_budget_survives_reclaim(catalog_client, kind, limit):
    conv = await conversation(catalog_client)
    key = uuid4().hex
    first, _ = await claim(catalog_client, conv.id, key=key)
    async with tx(catalog_client) as pair:
        await state.consume_budget(*pair, conv.id, first["id"], 1, kind)
        await state.finish_turn(*pair, conv.id, first["id"], 1, {}, "FAILED", "AI_PROVIDER_TIMEOUT")
    again, run = await claim(catalog_client, conv.id, key=key)
    assert run and again[kind + "_calls"] == 1
    async with tx(catalog_client) as pair:
        for _ in range(limit - 1):
            await state.consume_budget(*pair, conv.id, first["id"], 2, kind)
        with pytest.raises(Problem) as exc:
            await state.consume_budget(*pair, conv.id, first["id"], 2, kind)
        assert exc.value.code == "AI_BUDGET_EXCEEDED"


async def test_brief_default_date_persisted_but_original_request_is_idempotent(catalog_client):
    conv = await conversation(catalog_client)
    key = uuid4().hex
    async with tx(catalog_client) as pair:
        first, _ = await state.claim_turn(*pair, conv.id, {"day": None}, key, "BRIEF")
    assert first["request_body"]["day"] is None
    assert first["resolved_day"] is not None
    async with tx(catalog_client) as pair:
        again, run = await state.claim_turn(*pair, conv.id, {"day": None}, key, "BRIEF")
        assert again["request_body"] == first["request_body"] and not run
        assert again["resolved_day"] == first["resolved_day"]


async def test_finish_stores_only_public_response_and_sanitized_errors(catalog_client):
    conv = await conversation(catalog_client)
    turn, _ = await claim(catalog_client, conv.id)
    async with tx(catalog_client) as pair:
        with pytest.raises(Problem) as exc:
            await state.finish_turn(
                *pair,
                conv.id,
                turn["id"],
                1,
                {"answer": "文本", "payload": {"api_key": "sensitive"}},
                "COMPLETED",
            )
        assert exc.value.code == "INVALID_AI_RESPONSE"
        result = await state.finish_turn(
            *pair,
            conv.id,
            turn["id"],
            1,
            {"answer": "已读取结果", "evidence": []},
            "FAILED",
            "SAFE_UNKNOWN_ERROR",
        )
        assert result.answer == "已读取结果" and "SAFE_UNKNOWN_ERROR" not in result.error_message


async def test_proposal_edit_repreview_reject_and_stale_approval(catalog_client, sale):
    conv, turn, proposal = await proposed(catalog_client, sale)
    body = DRAFT_INPUT.validate_python({"kind": "SALES", "order": sale | {"reason": "改用途"}})
    edit = ProposalEdit(expected_revision=1, draft=body)
    key = uuid4().hex
    async with tx(catalog_client) as pair:
        changed = await state.edit_proposal(*pair, proposal.id, edit, key)
        assert (
            changed.revision == 2
            and changed.preview.confirmation_hash != proposal.preview.confirmation_hash
        )
        assert (await state.edit_proposal(*pair, proposal.id, edit, key)).revision == 2
        with pytest.raises(Problem) as exc:
            await state.approve_proposal(*pair, proposal.id, approved(proposal), uuid4().hex)
        assert exc.value.code == "AI_PROPOSAL_CHANGED"
        rejection = await state.reject_proposal(
            *pair, proposal.id, ProposalRejection(expected_revision=2), uuid4().hex
        )
        assert rejection.status == "REJECTED"
        assert (await state.read_turn(*pair, turn["id"])).state == "COMPLETED"
        with pytest.raises(Problem) as exc:
            await state.approve_proposal(*pair, proposal.id, approved(changed), uuid4().hex)
        assert exc.value.code == "AI_PROPOSAL_CLOSED"


async def test_expired_proposal_does_not_create(catalog_client, sale):
    _, _, proposal = await proposed(catalog_client, sale)
    admin_execute(
        "UPDATE forge.assistant_proposals SET expires_at=now()-interval '1 second' WHERE id=:id",
        {"id": proposal.id},
    )
    async with tx(catalog_client) as pair:
        view = await state.proposal_detail(*pair, proposal.id)
        assert view.status == "EXPIRED" and view.preview is None
        with pytest.raises(Problem) as exc:
            await state.approve_proposal(*pair, proposal.id, approved(proposal), uuid4().hex)
        assert exc.value.code == "AI_PROPOSAL_CLOSED"


async def test_concurrent_approval_and_permanent_receipt_after_history_delete(catalog_client, sale):
    conv, turn, proposal = await proposed(catalog_client, sale)

    async def approve():
        async with tx(catalog_client) as pair:
            return await state.approve_proposal(*pair, proposal.id, approved(proposal), uuid4().hex)

    a, b = await asyncio.gather(approve(), approve())
    assert a.receipt == b.receipt and a.receipt.status == "DRAFT"
    async with tx(catalog_client) as (db, ctx):
        assert (await state.read_turn(db, ctx, turn["id"])).state == "COMPLETED"
        assert (await db.execute(text("SELECT count(*) FROM forge.sales_orders"))).scalar_one() == 1
        assert (
            await db.execute(text("SELECT count(*) FROM forge.assistant_draft_receipts"))
        ).scalar_one() == 1
        await state.delete_conversation(db, ctx, conv.id, uuid4().hex)
    admin_execute(
        "UPDATE forge.idempotency_keys SET expires_at=now()-interval '1 day' "
        "WHERE organization_id=:org",
        {"org": ctx.organization_id},
    )
    admin_execute(
        "UPDATE forge.assistant_conversations SET expires_at=now()-interval '1 day' WHERE id=:id",
        {"id": conv.id},
    )
    replay = await approve()
    assert replay.receipt == a.receipt and replay.preview is None
    async with tx(catalog_client) as (db, ctx):
        raw = (
            await db.execute(
                text("SELECT prompt,request_body,response FROM forge.assistant_turns WHERE id=:id"),
                {"id": turn["id"]},
            )
        ).one()
        assert tuple(raw) == ("", {}, None)
        with pytest.raises(Problem) as exc:
            await state.conversation_detail(db, ctx, conv.id)
        assert exc.value.code == "AI_HISTORY_EXPIRED"
        changed = ProposalApproval(expected_revision=1, confirmation_hash="0" * 64)
        with pytest.raises(Problem) as exc:
            await state.approve_proposal(db, ctx, proposal.id, changed, uuid4().hex)
        assert exc.value.code == "AI_PROPOSAL_CHANGED"


async def test_approval_receipt_fault_rolls_back_order_audit_outbox(
    catalog_client, sale, monkeypatch
):
    _, _, proposal = await proposed(catalog_client, sale)
    original = state._audit

    async def fault(db, ctx, action, resource_type, id, after):
        if action == "ai.proposal.approved":
            raise Problem(503, "TEST_ATOMIC_ROLLBACK", "fixture fault")
        return await original(db, ctx, action, resource_type, id, after)

    monkeypatch.setattr(state, "_audit", fault)
    with pytest.raises(Problem):
        async with tx(catalog_client) as pair:
            await state.approve_proposal(*pair, proposal.id, approved(proposal), uuid4().hex)
    async with tx(catalog_client) as (db, ctx):
        for table in ("sales_orders", "assistant_draft_receipts"):
            assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar_one() == 0
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.audit_events WHERE action='sales.order.create'")
            )
        ).scalar_one() == 0
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.outbox_events WHERE event_type='sales.order.create'"
                )
            )
        ).scalar_one() == 0
        assert (await state.proposal_detail(db, ctx, proposal.id)).status == "PENDING"
    monkeypatch.setattr(state, "_audit", original)
    async with tx(catalog_client) as pair:
        assert (
            await state.approve_proposal(*pair, proposal.id, approved(proposal), uuid4().hex)
        ).status == "CREATED"


async def test_created_draft_retains_complete_actor_request_and_conversation_origin(
    catalog_client, sale
):
    conv, turn, proposal = await proposed(catalog_client, sale)
    request_id = "reviewed-creation-origin-" + uuid4().hex
    async with sessions.begin() as db:
        ctx = await state.authorize(db, catalog_client.cookies["forge_session"], request_id)
        created = await state.approve_proposal(
            db, ctx, proposal.id, approved(proposal), uuid4().hex
        )
        receipt = (
            (
                await db.execute(
                    text("SELECT * FROM forge.assistant_draft_receipts WHERE proposal_id=:id"),
                    {"id": proposal.id},
                )
            )
            .mappings()
            .one()
        )
        assert receipt["organization_id"] == ctx.organization_id
        assert receipt["owner_id"] == ctx.user_id
        assert receipt["conversation_id"] == conv.id and receipt["turn_id"] == turn["id"]
        assert receipt["request_id"] == request_id
        assert str(receipt["sales_order_id"]) == str(created.receipt.id)
        audits = (
            (
                await db.execute(
                    text(
                        "SELECT actor_type,actor_id,source,request_id,action,after "
                        "FROM forge.audit_events WHERE request_id=:rid ORDER BY action"
                    ),
                    {"rid": request_id},
                )
            )
            .mappings()
            .all()
        )
        assert {row["action"] for row in audits} == {
            "sales.order.create",
            "ai.proposal.approved",
        }
        assert all(
            (row["actor_type"], row["actor_id"], row["source"], row["request_id"])
            == ("USER", ctx.user_id, "AI", request_id)
            for row in audits
        )
        approval = next(row for row in audits if row["action"] == "ai.proposal.approved")
        assert approval["after"]["conversation_id"] == str(conv.id)
        assert approval["after"]["turn_id"] == str(turn["id"])
        assert approval["after"]["document_id"] == str(created.receipt.id)
        outbox = (
            await db.execute(
                text("SELECT event_type FROM forge.outbox_events WHERE request_id=:rid"),
                {"rid": request_id},
            )
        ).scalars()
        assert set(outbox) == {"sales.order.create", "ai.proposal.approved"}


async def test_expired_conversation_is_hidden(catalog_client):
    conv = await conversation(catalog_client)
    admin_execute(
        "UPDATE forge.assistant_conversations SET expires_at=:expiry WHERE id=:id",
        {"id": conv.id, "expiry": datetime.now(UTC) - timedelta(seconds=1)},
    )
    async with tx(catalog_client) as pair:
        assert (await state.list_conversations(*pair)).total == 0
        with pytest.raises(Problem) as exc:
            await state.require_conversation(*pair, conv.id)
        assert exc.value.code == "AI_HISTORY_EXPIRED"


async def test_authorize_supports_read_only_snapshot(catalog_client):
    conv = await conversation(catalog_client)
    async with sessions.begin() as db:
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        ctx = await state.authorize(
            db, catalog_client.cookies["forge_session"], "readonly-auth", conv.id
        )
        assert ctx.source == "AI" and ctx.conversation_id == conv.id
        assert (await state.conversation_detail(db, ctx, conv.id)).id == conv.id


async def test_same_turn_proposal_repeat_keeps_user_edited_preview(catalog_client, sale):
    conv, turn, proposal = await proposed(catalog_client, sale)
    original = DRAFT_INPUT.validate_python({"kind": "SALES", "order": sale})
    edited = DRAFT_INPUT.validate_python({"kind": "SALES", "order": sale | {"reason": "用户修改"}})
    async with tx(catalog_client) as pair:
        changed = await state.edit_proposal(
            *pair, proposal.id, ProposalEdit(expected_revision=1, draft=edited), uuid4().hex
        )
        replay = await state.create_proposal(*pair, turn["id"], 1, original)
        assert replay.id == proposal.id and replay.revision == changed.revision
        assert replay.preview.order.reason == "用户修改"


@pytest.mark.parametrize(
    "permission", ["ai.draft.create", "sales.order.write", "product.price.read"]
)
async def test_permanent_receipt_still_requires_current_business_authority(
    catalog_client,
    sale,
    identities,
    permission,
):
    _, _, proposal = await proposed(catalog_client, sale)
    async with tx(catalog_client) as pair:
        await state.approve_proposal(*pair, proposal.id, approved(proposal), uuid4().hex)
    admin_execute(
        "DELETE FROM forge.role_permissions WHERE organization_id=:org AND role_id=:role "
        "AND permission_code=:permission",
        identities[0] | {"permission": permission},
    )
    async with tx(catalog_client) as pair:
        with pytest.raises(Problem) as exc:
            await state.approve_proposal(*pair, proposal.id, approved(proposal), uuid4().hex)
        assert exc.value.code == "PERMISSION_DENIED"


async def test_permanent_receipt_survives_provider_change_without_old_preview(
    catalog_client, sale, identities
):
    conv, _, proposal = await proposed(catalog_client, sale)
    async with tx(catalog_client) as pair:
        first = await state.approve_proposal(*pair, proposal.id, approved(proposal), uuid4().hex)
    admin_execute(
        "INSERT INTO forge.ai_provider_settings "
        "(organization_id,name,base_url,model,updated_by) "
        "VALUES(:org,'Other','https://example.test/v1','test',:user)",
        identities[0],
    )
    async with tx(catalog_client) as pair:
        replay = await state.approve_proposal(*pair, proposal.id, approved(proposal), uuid4().hex)
        assert replay.receipt == first.receipt and replay.preview is None
        with pytest.raises(Problem) as exc:
            await state.conversation_detail(*pair, conv.id)
        assert exc.value.code == "AI_CONTEXT_CHANGED"


async def test_brief_retry_reuses_original_body_and_frozen_resolved_day(catalog_client):
    conv = await conversation(catalog_client)
    key = uuid4().hex
    async with tx(catalog_client) as pair:
        first, _ = await state.claim_turn(*pair, conv.id, {"day": None}, key, "BRIEF")
        await state.finish_turn(*pair, conv.id, first["id"], 1, {}, "FAILED", "AI_PROVIDER_TIMEOUT")
    async with tx(catalog_client) as pair:
        next_turn, run = await state.claim_turn(*pair, conv.id, first["request_body"], key, "BRIEF")
        assert run and next_turn["id"] == first["id"]
        assert next_turn["resolved_day"] == first["resolved_day"]


async def test_stale_attempt_cannot_finish_or_create_proposal(catalog_client, sale):
    conv = await conversation(catalog_client)
    key = uuid4().hex
    turn, _ = await claim(catalog_client, conv.id, key=key)
    admin_execute(
        "UPDATE forge.assistant_turns SET lease_until=now()-interval '1 second' WHERE id=:id", turn
    )
    await claim(catalog_client, conv.id, key=key)
    async with tx(catalog_client) as pair:
        for operation in (
            lambda: state.finish_turn(
                *pair, conv.id, turn["id"], 1, {"answer": "旧执行"}, "COMPLETED"
            ),
            lambda: state.create_proposal(
                *pair, turn["id"], 1, DRAFT_INPUT.validate_python({"kind": "SALES", "order": sale})
            ),
        ):
            with pytest.raises(Problem) as exc:
                await operation()
            assert exc.value.code == "AI_TURN_FENCED"


async def test_review_cannot_race_running_turn_before_interrupt_is_published(catalog_client, sale):
    conv = await conversation(catalog_client)
    turn, _ = await claim(catalog_client, conv.id)
    async with tx(catalog_client) as pair:
        proposal = await state.create_proposal(
            *pair, turn["id"], 1, DRAFT_INPUT.validate_python({"kind": "SALES", "order": sale})
        )
    async with tx(catalog_client) as pair:
        with pytest.raises(Problem) as exc:
            await state.approve_proposal(*pair, proposal.id, approved(proposal), uuid4().hex)
        assert exc.value.code == "AI_REVIEW_NOT_READY"
        await state.finish_turn(*pair, conv.id, turn["id"], 1, {"answer": "请复核"}, "WAITING")
    async with tx(catalog_client) as pair:
        assert (
            await state.approve_proposal(*pair, proposal.id, approved(proposal), uuid4().hex)
        ).status == "CREATED"


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "不能保存\x00字符"},
        {"message": "😀" * 4000, "selected_ids": [str(uuid4()) for _ in range(20)]},
    ],
)
async def test_message_json_limits_fail_before_database_constraint(catalog_client, payload):
    conv = await conversation(catalog_client)
    async with tx(catalog_client) as pair:
        with pytest.raises(Problem) as exc:
            await state.claim_turn(*pair, conv.id, payload, uuid4().hex)
        assert exc.value.status == 422 and exc.value.code == "INVALID_AI_TURN"
