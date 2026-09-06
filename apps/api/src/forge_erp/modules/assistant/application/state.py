"""Private assistant progress, bounded leases, and durable reviewed creation receipts."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import set_tenant
from forge_erp.core.errors import Problem
from forge_erp.core.idempotency import execute_once
from forge_erp.core.security import fingerprint
from forge_erp.modules.assistant.application import drafts, providers
from forge_erp.modules.assistant.domain.chat import (
    Activity,
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
from forge_erp.modules.assistant.domain.drafts import DRAFT_INPUT, DraftInput, DraftPreview
from forge_erp.modules.assistant.domain.tools import Evidence
from forge_erp.modules.audit.service import record_mutation
from forge_erp.modules.identity.application.commands import load_permissions, resolve_context

MAX_ATTEMPTS = 2
MAX_MODELS = 5
MAX_TOOLS = 8
ERROR_MESSAGES = {
    "AI_NOT_CONFIGURED": "请先在设置中配置并启用模型服务。",
    "AI_BUDGET_EXCEEDED": "本轮已达到运行上限，请缩小问题范围后开始新一轮。",
    "AI_CONTEXT_CHANGED": "权限或模型设置已变化，请开始新对话。",
    "AI_PROVIDER_TIMEOUT": "模型服务未及时响应，可重试本轮。",
    "AI_DRAFT_CHANGED": "开单依据已变化，请重新预览并复核。",
    "AI_HISTORY_EXPIRED": "对话正文已到期，请开始新对话。",
    "AI_STATE_EXPIRED": "对话正文已到期，请开始新对话。",
    "AI_ENTITY_UNRESOLVED": "请明确商品、往来单位、仓库和单位，再生成草稿。",
    "AI_QUANTITY_UNRESOLVED": "请用数字明确每行数量。",
    "AI_PRICE_UNRESOLVED": "请明确手工单价，再生成草稿。",
    "AI_TOOL_ARGUMENTS_INVALID": "查询条件不完整或格式无效，请补充后重试。",
    "AI_TURN_TIMEOUT": "本轮处理超时，可重试本轮或缩小问题范围。",
    "AI_PROVIDER_AUTH_FAILED": "模型服务凭据无效，请管理员检查模型服务设置。",
    "AI_PROVIDER_AUTH": "模型服务凭据无效，请管理员检查模型服务设置。",
    "AI_PROVIDER_RATE_LIMITED": "模型服务暂时繁忙，请稍后重试。",
    "AI_PROVIDER_RATE_LIMIT": "模型服务暂时繁忙，请稍后重试。",
    "AI_PROVIDER_UNAVAILABLE": "模型服务暂时不可用，请稍后重试。",
    "AI_PROVIDER_ERROR": "模型服务暂时不可用，请检查设置或稍后重试。",
    "AI_PROVIDER_CONNECTION": "无法连接模型服务，请检查地址或稍后重试。",
    "AI_PROVIDER_ENCODING": "模型服务返回了无法读取的内容，请检查服务设置。",
    "AI_PROTOCOL_UNSUPPORTED": "模型未按约定返回结果，请检查模型设置。",
    "AI_RESPONSE_TRUNCATED": "模型输出被截断，请缩小请求范围后重试。",
    "AI_DECISION_INVALID": "模型未返回可处理的操作，请重试或调整模型设置。",
    "AI_EVIDENCE_INVALID": "本轮没有取得可核对的系统来源，请明确查询条件。",
    "AI_TOOL_NOT_ALLOWED": "此操作未向助手开放，请使用对应业务工作台。",
    "AI_CHAT_SCOPE": "回复涉及尚未核对的业务信息，请明确查询条件后再试。",
    "AI_STREAM_UNSUPPORTED": "此模型服务未返回流式文本，请管理员检查模型设置。",
}


def params(ctx: RuntimeContext, **values) -> dict:
    return {"org": ctx.organization_id, "owner": ctx.user_id, **values}


def _key(key: str) -> None:
    if not 8 <= len(key) <= 128:
        raise Problem(422, "INVALID_IDEMPOTENCY_KEY", "Key must contain 8–128 characters")


async def _scope(db: AsyncSession, ctx: RuntimeContext) -> None:
    await set_tenant(db, ctx.organization_id)
    await db.execute(
        text("SELECT set_config('app.user_id',:owner,true)"), {"owner": str(ctx.user_id)}
    )
    active = (
        await db.execute(
            text(
                "SELECT u.active AND o.active FROM forge.users u JOIN forge.organizations o "
                "ON o.id=u.organization_id WHERE u.organization_id=:org AND u.id=:owner"
            ),
            params(ctx),
        )
    ).scalar_one_or_none()
    if not active:
        raise Problem(401, "UNAUTHENTICATED", "请重新登录")
    current = await load_permissions(db, ctx.organization_id, ctx.user_id)
    if "ai.use" not in current:
        raise Problem(403, "PERMISSION_DENIED", "没有使用助手的权限")
    if current != ctx.permissions:
        raise Problem(409, "AI_CONTEXT_CHANGED", "权限已变化，请重新读取当前账户")
    ctx.require("ai.use")


async def permissions_hash(db: AsyncSession, ctx: RuntimeContext) -> str:
    # Provider updates take the exclusive version of this same transaction lock.
    await db.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:lock,0))"),
        {"lock": f"ai-provider:{ctx.organization_id}"},
    )
    return fingerprint(
        {
            "permissions": sorted(ctx.permissions),
            "provider_version": await providers.version(db, ctx),
        }
    )


async def authorize(
    db: AsyncSession, token: str, rid: str, conversation_id: UUID | None = None
) -> RuntimeContext:
    ctx = replace(
        await resolve_context(db, token, rid), source="AI", conversation_id=conversation_id
    )
    await _scope(db, ctx)
    if conversation_id is not None:
        await require_conversation(db, ctx, conversation_id)
    return ctx


async def require_conversation(
    db: AsyncSession,
    ctx: RuntimeContext,
    id: UUID,
    lock: bool = False,
    require_current: bool = True,
) -> dict:
    await _scope(db, ctx)
    row = (
        (
            await db.execute(
                text(
                    "SELECT * FROM forge.assistant_conversations "
                    "WHERE organization_id=:org AND owner_id=:owner AND id=:id"
                    + (" FOR UPDATE" if lock else "")
                ),
                params(ctx, id=id),
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "NOT_FOUND", "对话不存在或无权访问")
    if require_current:
        if row["deleted_at"] or row["expires_at"] <= datetime.now(UTC):
            raise Problem(410, "AI_HISTORY_EXPIRED", "对话正文已到期或删除，请开始新对话")
        if row["permissions_hash"] != await permissions_hash(db, ctx):
            raise Problem(409, "AI_CONTEXT_CHANGED", "权限或模型设置已变化，请开始新对话")
    return dict(row)


async def _conversation_lock(db, ctx, id):
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock,0))"),
        {"lock": f"ai-conversation:{ctx.organization_id}:{ctx.user_id}:{id}"},
    )


async def _audit(db, ctx, action, resource_type, id, after):
    await record_mutation(db, replace(ctx, source="AI"), action, resource_type, id, None, after)


async def create_conversation(db, ctx, body: ConversationInput, key: str) -> ConversationRead:
    await _scope(db, ctx)
    if "\x00" in body.title:
        raise Problem(422, "INVALID_AI_TITLE", "对话标题包含无法保存的字符")
    value_hash = await permissions_hash(db, ctx)

    async def create():
        row = (
            await db.execute(
                text(
                    "INSERT INTO forge.assistant_conversations "
                    "(organization_id,owner_id,permissions_hash,title) "
                    "VALUES(:org,:owner,:hash,:title) "
                    "RETURNING id"
                ),
                params(ctx, hash=value_hash, title=body.title),
            )
        ).one()
        await _audit(
            db, ctx, "ai.conversation.created", "assistant_conversation", row.id, {"version": 1}
        )
        return {"id": str(row.id)}

    receipt = await execute_once(
        db, ctx, "ai.conversation.create", key, body.model_dump(mode="json"), create
    )
    return await conversation_detail(db, ctx, UUID(receipt["id"]))


async def list_conversations(db, ctx, page: int = 1, page_size: int = 25) -> ConversationsPage:
    await _scope(db, ctx)
    if not 1 <= page <= 1000 or not 1 <= page_size <= 25:
        raise Problem(422, "INVALID_PAGE", "分页范围无效")
    values = params(
        ctx, hash=await permissions_hash(db, ctx), limit=page_size, offset=(page - 1) * page_size
    )
    clause = (
        " FROM forge.assistant_conversations WHERE organization_id=:org AND owner_id=:owner "
        "AND deleted_at IS NULL AND expires_at>clock_timestamp() AND permissions_hash=:hash"
    )
    total = (await db.execute(text("SELECT count(*)" + clause), values)).scalar_one()
    rows = (
        await db.execute(
            text(
                "SELECT *"
                + clause
                + " ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset"
            ),
            values,
        )
    ).mappings()
    return ConversationsPage(
        items=[ConversationSummary.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


async def conversation_detail(db, ctx, id: UUID) -> ConversationRead:
    row = await require_conversation(db, ctx, id)
    turns = (
        (
            await db.execute(
                text(
                    "SELECT * FROM forge.assistant_turns WHERE organization_id=:org AND "
                    "owner_id=:owner "
                    "AND conversation_id=:id AND expires_at>clock_timestamp() "
                    "ORDER BY created_at DESC,id DESC LIMIT 21"
                ),
                params(ctx, id=id),
            )
        )
        .mappings()
        .all()
    )
    return ConversationRead(
        **ConversationSummary.model_validate(row).model_dump(),
        turns=[await _turn_read(db, ctx, dict(turn)) for turn in reversed(turns[:20])],
        older_turns_omitted=len(turns) > 20,
    )


async def delete_conversation(db, ctx, id: UUID, key: str) -> ConversationDeleted:
    await _conversation_lock(db, ctx, id)
    row = await require_conversation(db, ctx, id, lock=True, require_current=False)

    async def delete():
        if row["deleted_at"] is None:
            values = params(ctx, id=id)
            await db.execute(
                text(
                    "UPDATE forge.assistant_conversations SET deleted_at=clock_timestamp(),"
                    "title='已删除对话' WHERE organization_id=:org AND owner_id=:owner AND id=:id"
                ),
                values,
            )
            await db.execute(
                text(
                    "UPDATE forge.assistant_turns SET prompt='',request_body='{}',response=NULL,"
                    "state='FAILED',error_code='AI_HISTORY_EXPIRED',lease_until=NULL "
                    "WHERE organization_id=:org AND owner_id=:owner AND conversation_id=:id"
                ),
                values,
            )
            await db.execute(
                text(
                    "UPDATE forge.assistant_proposals SET body='{}',preview='{}',"
                    "status=CASE WHEN status='PENDING' THEN 'EXPIRED' ELSE status END "
                    "WHERE organization_id=:org AND owner_id=:owner AND conversation_id=:id"
                ),
                values,
            )
            for table in ("assistant_checkpoint_writes", "assistant_checkpoints"):
                await db.execute(
                    text(
                        f"DELETE FROM forge.{table} WHERE organization_id=:org "
                        "AND owner_id=:owner AND conversation_id=:id"
                    ),
                    values,
                )
            await _audit(
                db, ctx, "ai.conversation.deleted", "assistant_conversation", id, {"version": 1}
            )
        return {"id": str(id), "deleted": True}

    return ConversationDeleted.model_validate(
        await execute_once(
            db,
            ctx,
            f"ai.conversation.delete:{id}",
            key,
            {},
            delete,
        )
    )


def _can_retry(row) -> bool:
    return (
        row["state"] == "FAILED"
        or (
            row["state"] == "RUNNING"
            and (row["lease_until"] is None or row["lease_until"] <= datetime.now(UTC))
        )
    ) and (
        row["attempts"] < MAX_ATTEMPTS
        and row["model_calls"] < MAX_MODELS
        and row["tool_calls"] < MAX_TOOLS
        and row["expires_at"] > datetime.now(UTC)
    )


async def claim_turn(
    db,
    ctx,
    conversation_id: UUID,
    request_body: dict,
    key: str,
    kind: str = "CHAT",
) -> tuple[dict, bool]:
    _key(key)
    if kind not in {"CHAT", "BRIEF"}:
        raise Problem(422, "INVALID_AI_TURN", "不支持此类助手请求")
    try:
        validated = (
            MessageInput.model_validate(request_body)
            if kind == "CHAT"
            else BriefInput.model_validate(request_body)
        )
    except ValidationError as exc:
        raise Problem(422, "INVALID_AI_TURN", "助手请求格式无效") from exc
    original = validated.model_dump(mode="json")
    encoded = json.dumps(original, ensure_ascii=False)
    if (kind == "CHAT" and "\x00" in original["message"]) or len(encoded.encode()) > 16_384:
        raise Problem(422, "INVALID_AI_TURN", "问题或选定资料超出可保存范围，请缩短内容")
    value_hash = fingerprint({"kind": kind, "body": original})
    await _conversation_lock(db, ctx, conversation_id)
    conversation = await require_conversation(db, ctx, conversation_id, lock=True)
    values = params(ctx, conversation=conversation_id, key=key)
    previous = (
        (
            await db.execute(
                text(
                    "SELECT * FROM forge.assistant_turns WHERE organization_id=:org AND "
                    "owner_id=:owner "
                    "AND conversation_id=:conversation AND idempotency_key=:key FOR UPDATE"
                ),
                values,
            )
        )
        .mappings()
        .first()
    )
    if previous:
        if previous["request_hash"] != value_hash:
            raise Problem(409, "IDEMPOTENCY_KEY_REUSED", "此请求标识已用于不同的问题")
        if not _can_retry(previous):
            return dict(previous), False
    active = (
        await db.execute(
            text(
                "SELECT id FROM forge.assistant_turns WHERE organization_id=:org AND "
                "owner_id=:owner "
                "AND conversation_id=:conversation AND state='RUNNING' AND "
                "lease_until>clock_timestamp() "
                "LIMIT 1"
            ),
            values,
        )
    ).first()
    if active:
        raise Problem(409, "AI_CONVERSATION_BUSY", "本对话仍有正在处理的问题，请稍候")
    if previous:
        public = _response(previous["response"] or {})
        for entry in public["activity"]:
            if entry["state"] == "running":
                entry.update(state="failed", finished_at=datetime.now(UTC).isoformat())
        row = (
            (
                await db.execute(
                    text(
                        "UPDATE forge.assistant_turns SET "
                        "attempts=attempts+1,state='RUNNING',error_code=NULL,"
                        "lease_until=clock_timestamp()+interval '120 seconds',request_id=:rid,"
                        "response=CAST(:response AS jsonb) "
                        "WHERE organization_id=:org AND owner_id=:owner AND id=:id RETURNING *"
                    ),
                    params(ctx, id=previous["id"], rid=ctx.request_id, response=json.dumps(public)),
                )
            )
            .mappings()
            .one()
        )
    else:
        actual_day = None
        if kind == "CHAT":
            prompt = original["message"]
        else:
            actual_day = (
                date.fromisoformat(original["day"])
                if original["day"]
                else (
                    datetime.now(ZoneInfo(settings().business_timezone)).date() - timedelta(days=1)
                )
            )
            prompt = f"每日简报 · {actual_day}"
        row = (
            (
                await db.execute(
                    text(
                        "INSERT INTO "
                        "forge.assistant_turns(organization_id,owner_id,conversation_id,"
                        "idempotency_key,"
                        "request_hash,prompt,kind,attempts,lease_until,request_id,request_body,"
                        "expires_at,resolved_day) "
                        "VALUES(:org,:owner,:conversation,:key,:hash,:prompt,:kind,1,"
                        "clock_timestamp()+interval '120 seconds',:rid,CAST(:body AS "
                        "jsonb),:expiry,:day) RETURNING *"
                    ),
                    {
                        **values,
                        "hash": value_hash,
                        "prompt": prompt,
                        "kind": kind,
                        "rid": ctx.request_id,
                        "body": json.dumps(original),
                        "expiry": conversation["expires_at"],
                        "day": actual_day,
                    },
                )
            )
            .mappings()
            .one()
        )
    await _audit(
        db,
        ctx,
        "ai.turn.claimed",
        "assistant_turn",
        row["id"],
        {
            "version": row["attempts"],
            "conversation_id": str(conversation_id),
            "kind": kind,
        },
    )
    return dict(row), True


async def _turn(db, ctx, id: UUID, *, lock=False) -> dict:
    row = (
        (
            await db.execute(
                text(
                    "SELECT * FROM forge.assistant_turns WHERE organization_id=:org "
                    "AND owner_id=:owner AND id=:id" + (" FOR UPDATE" if lock else "")
                ),
                params(ctx, id=id),
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "NOT_FOUND", "助手记录不存在或无权访问")
    return dict(row)


def _fence(row, conversation_id, attempt):
    if (
        row["conversation_id"] != conversation_id
        or row["attempts"] != attempt
        or row["state"] != "RUNNING"
        or row["lease_until"] is None
        or row["lease_until"] <= datetime.now(UTC)
        or row["expires_at"] <= datetime.now(UTC)
    ):
        raise Problem(409, "AI_TURN_FENCED", "本次执行已失效，请读取最新处理结果")


async def consume_budget(
    db, ctx, conversation_id: UUID, turn_id: UUID, attempt: int, kind: Literal["model", "tool"]
) -> None:
    await require_conversation(db, ctx, conversation_id)
    row = await _turn(db, ctx, turn_id, lock=True)
    _fence(row, conversation_id, attempt)
    if kind not in {"model", "tool"}:
        raise Problem(422, "INVALID_AI_BUDGET", "无效的运行计数")
    column, limit = ("model_calls", MAX_MODELS) if kind == "model" else ("tool_calls", MAX_TOOLS)
    if row[column] >= limit:
        raise Problem(409, "AI_BUDGET_EXCEEDED", "本轮已达到运行上限")
    await db.execute(
        text(
            f"UPDATE forge.assistant_turns SET {column}={column}+1 "
            "WHERE organization_id=:org AND owner_id=:owner AND id=:id"
        ),
        params(ctx, id=turn_id),
    )


def _response(response: dict) -> dict:
    if set(response) - {"answer", "evidence", "activity", "interaction", "guided"}:
        raise Problem(422, "INVALID_AI_RESPONSE", "仅允许保存公开回答与事实证据")
    answer = response.get("answer")
    if answer is not None and (
        not isinstance(answer, str) or len(answer) > 8000 or "\x00" in answer
    ):
        raise Problem(422, "INVALID_AI_RESPONSE", "回答长度或格式无效")
    evidence = response.get("evidence", [])
    if not isinstance(evidence, list) or len(evidence) > MAX_TOOLS:
        raise Problem(422, "INVALID_AI_RESPONSE", "事实证据数量无效")
    try:
        result = {
            "answer": answer,
            "evidence": [Evidence.model_validate(x).model_dump(mode="json") for x in evidence],
            "activity": [
                Activity.model_validate(x).model_dump(mode="json")
                for x in response.get("activity", [])
            ],
            "interaction": response.get("interaction", "business"),
            "guided": response.get("guided", False),
        }
        if (
            len(result["activity"]) > 32
            or result["interaction"] not in {"business", "casual"}
            or not isinstance(result["guided"], bool)
        ):
            raise ValueError("invalid public progress")
        if len(json.dumps(result, ensure_ascii=False).encode()) > 1_048_576:
            raise ValueError("too large")
    except (ValidationError, ValueError) as exc:
        raise Problem(422, "INVALID_AI_RESPONSE", "事实证据格式或长度无效") from exc
    return result


async def progress(
    db, ctx, conversation_id, turn_id, attempt, activity: Activity, evidence: Evidence | None = None
) -> TurnRead:
    await require_conversation(db, ctx, conversation_id)
    row = await _turn(db, ctx, turn_id, lock=True)
    _fence(row, conversation_id, attempt)
    public = _response(row["response"] or {})
    entries = public["activity"]
    previous = next((i for i, entry in enumerate(entries) if entry["id"] == activity.id), None)
    if previous is None:
        entries.append(activity.model_dump(mode="json"))
    else:
        entries[previous] = activity.model_dump(mode="json")
    if evidence is not None:
        public["evidence"] = [e for e in public["evidence"] if e["id"] != evidence.id]
        public["evidence"].append(evidence.model_dump(mode="json"))
    public = _response(public)
    await db.execute(
        text(
            "UPDATE forge.assistant_turns SET response=CAST(:response AS jsonb) "
            "WHERE organization_id=:org AND owner_id=:owner AND id=:id"
        ),
        params(ctx, id=turn_id, response=json.dumps(public)),
    )
    return await read_turn(db, ctx, turn_id)


async def finish_turn(
    db,
    ctx,
    conversation_id: UUID,
    turn_id: UUID,
    attempt: int,
    response: dict,
    state: str,
    error_code: str | None = None,
) -> TurnRead:
    await require_conversation(db, ctx, conversation_id)
    row = await _turn(db, ctx, turn_id, lock=True)
    _fence(row, conversation_id, attempt)
    if state not in {"WAITING", "COMPLETED", "FAILED"}:
        raise Problem(422, "INVALID_AI_STATE", "无效的助手状态")
    saved = _response(row["response"] or {})
    public = _response({**saved, **response, "activity": saved["activity"]})
    for entry in public["activity"]:
        if entry["state"] == "running":
            entry.update(state="failed", finished_at=datetime.now(UTC).isoformat())
    if error_code is not None and (
        not error_code.isascii()
        or not error_code.replace("_", "").isalnum()
        or len(error_code) > 120
    ):
        raise Problem(422, "INVALID_AI_ERROR", "无效的错误标识")
    await db.execute(
        text(
            "UPDATE forge.assistant_turns SET state=:state,response=CAST(:response AS jsonb),"
            "error_code=:error,lease_until=NULL WHERE organization_id=:org AND owner_id=:owner "
            "AND id=:id"
        ),
        params(ctx, id=turn_id, state=state, response=json.dumps(public), error=error_code),
    )
    await _audit(
        db,
        ctx,
        "ai.turn.finished",
        "assistant_turn",
        turn_id,
        {
            "version": attempt,
            "conversation_id": str(conversation_id),
            "state": state,
            "model_calls": row["model_calls"],
            "tool_calls": row["tool_calls"],
            "error_code": error_code,
        },
    )
    return await read_turn(db, ctx, turn_id)


async def read_turn(db, ctx, turn_id: UUID) -> TurnRead:
    await _scope(db, ctx)
    row = await _turn(db, ctx, turn_id)
    await require_conversation(db, ctx, row["conversation_id"])
    if row["expires_at"] <= datetime.now(UTC):
        raise Problem(410, "AI_HISTORY_EXPIRED", "本轮正文已到期")
    return await _turn_read(db, ctx, row)


async def _turn_read(db, ctx, row) -> TurnRead:
    proposal = (
        (
            await db.execute(
                text(
                    "SELECT * FROM forge.assistant_proposals "
                    "WHERE organization_id=:org AND owner_id=:owner AND turn_id=:turn ORDER BY "
                    "created_at LIMIT 1"
                ),
                params(ctx, turn=row["id"]),
            )
        )
        .mappings()
        .first()
    )
    response = _response(row["response"] or {})
    return TurnRead(
        id=row["id"],
        state=row["state"],
        prompt=row["prompt"],
        created_at=row["created_at"],
        **response,
        proposal=await _proposal_read(db, ctx, proposal) if proposal else None,
        error_code=row["error_code"],
        error_message=(
            ERROR_MESSAGES.get(row["error_code"], "助手暂时无法完成本轮，请重试或开始新问题。")
            if row["error_code"]
            else None
        ),
        model_calls=row["model_calls"],
        tool_calls=row["tool_calls"],
        can_retry=_can_retry(row),
    )


async def _proposal(db, ctx, id, *, lock=False):
    row = (
        (
            await db.execute(
                text(
                    "SELECT * FROM forge.assistant_proposals WHERE organization_id=:org "
                    "AND owner_id=:owner AND id=:id" + (" FOR UPDATE" if lock else "")
                ),
                params(ctx, id=id),
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "NOT_FOUND", "提案不存在或无权访问")
    return dict(row)


async def _receipt(db, ctx, proposal_id):
    return (
        (
            await db.execute(
                text(
                    "SELECT * FROM forge.assistant_draft_receipts "
                    "WHERE organization_id=:org AND owner_id=:owner AND proposal_id=:id"
                ),
                params(ctx, id=proposal_id),
            )
        )
        .mappings()
        .first()
    )


async def _proposal_read(db, ctx, row) -> ProposalRead:
    receipt = await _receipt(db, ctx, row["id"])
    expired = row["status"] == "PENDING" and row["expires_at"] <= datetime.now(UTC)
    return ProposalRead(
        id=row["id"],
        turn_id=row["turn_id"],
        kind=row["kind"],
        revision=row["revision"],
        status="EXPIRED" if expired else row["status"],
        expires_at=row["expires_at"],
        preview=DraftPreview.model_validate(row["preview"])
        if row["preview"] and not expired
        else None,
        receipt=DraftCreationReceipt.model_validate(receipt["receipt"]) if receipt else None,
    )


async def proposal_detail(db, ctx, id: UUID) -> ProposalRead:
    await _scope(db, ctx)
    row = await _proposal(db, ctx, id)
    await require_conversation(db, ctx, row["conversation_id"])
    return await _proposal_read(db, ctx, row)


async def create_proposal(db, ctx, turn_id: UUID, attempt: int, draft: DraftInput) -> ProposalRead:
    await _scope(db, ctx)
    turn = await _turn(db, ctx, turn_id)
    await _conversation_lock(db, ctx, turn["conversation_id"])
    await require_conversation(db, ctx, turn["conversation_id"], lock=True)
    turn = await _turn(db, ctx, turn_id, lock=True)
    previous = (
        (
            await db.execute(
                text(
                    "SELECT * FROM forge.assistant_proposals WHERE organization_id=:org "
                    "AND owner_id=:owner AND turn_id=:turn"
                ),
                params(ctx, turn=turn_id),
            )
        )
        .mappings()
        .first()
    )
    if previous:
        if turn["attempts"] != attempt:
            raise Problem(409, "AI_TURN_FENCED", "本次执行已失效，请读取最新处理结果")
        return await _proposal_read(db, ctx, previous)
    _fence(turn, turn["conversation_id"], attempt)
    shown = await drafts.preview_draft(db, ctx, draft)
    row = (
        (
            await db.execute(
                text(
                    "INSERT INTO forge.assistant_proposals "
                    "(organization_id,owner_id,conversation_id,turn_id,kind,body,preview,"
                    "confirmation_hash) "
                    "VALUES(:org,:owner,:conversation,:turn,:kind,CAST(:body AS "
                    "jsonb),CAST(:preview AS jsonb),:hash) RETURNING *"
                ),
                params(
                    ctx,
                    conversation=turn["conversation_id"],
                    turn=turn_id,
                    kind=draft.kind,
                    body=draft.model_dump_json(),
                    preview=shown.model_dump_json(),
                    hash=shown.confirmation_hash,
                ),
            )
        )
        .mappings()
        .one()
    )
    await _audit(
        db,
        ctx,
        "ai.proposal.created",
        "assistant_proposal",
        row["id"],
        {
            "version": 1,
            "conversation_id": str(turn["conversation_id"]),
            "turn_id": str(turn_id),
            "kind": draft.kind,
        },
    )
    return await _proposal_read(db, ctx, row)


async def _locked_proposal(db, ctx, id, *, require_current=True):
    await _scope(db, ctx)
    row = await _proposal(db, ctx, id)
    await _conversation_lock(db, ctx, row["conversation_id"])
    await require_conversation(
        db, ctx, row["conversation_id"], lock=True, require_current=require_current
    )
    ctx.require("ai.draft.create")
    return await _proposal(db, ctx, id, lock=True)


async def _review_ready(db, ctx, row):
    turn = await _turn(db, ctx, row["turn_id"], lock=True)
    if turn["state"] != "WAITING":
        raise Problem(409, "AI_REVIEW_NOT_READY", "本轮尚未进入复核状态，请读取最新结果")


def _pending(row, expected):
    if row["status"] != "PENDING" or row["expires_at"] <= datetime.now(UTC):
        raise Problem(409, "AI_PROPOSAL_CLOSED", "提案已处理或到期，请重新预览")
    if row["revision"] != expected:
        raise Problem(409, "AI_PROPOSAL_CHANGED", "提案已修改，请重新读取并复核")


async def edit_proposal(db, ctx, id: UUID, body: ProposalEdit, key: str) -> ProposalRead:
    row = await _locked_proposal(db, ctx, id)

    async def edit():
        _pending(row, body.expected_revision)
        await _review_ready(db, ctx, row)
        preview = await drafts.preview_draft(db, ctx, body.draft)
        await db.execute(
            text(
                "UPDATE forge.assistant_proposals SET revision=revision+1,kind=:kind,"
                "body=CAST(:body AS jsonb),preview=CAST(:preview AS jsonb),confirmation_hash=:hash,"
                "expires_at=clock_timestamp()+interval '30 minutes' WHERE organization_id=:org "
                "AND owner_id=:owner AND id=:id"
            ),
            params(
                ctx,
                id=id,
                kind=body.draft.kind,
                body=body.draft.model_dump_json(),
                preview=preview.model_dump_json(),
                hash=preview.confirmation_hash,
            ),
        )
        await _audit(
            db,
            ctx,
            "ai.proposal.edited",
            "assistant_proposal",
            id,
            {
                "version": row["revision"] + 1,
                "conversation_id": str(row["conversation_id"]),
                "turn_id": str(row["turn_id"]),
                "kind": body.draft.kind,
            },
        )
        return {"id": str(id), "revision": row["revision"] + 1}

    await execute_once(db, ctx, f"ai.proposal.edit:{id}", key, body.model_dump(mode="json"), edit)
    return await proposal_detail(db, ctx, id)


async def _complete_waiting(db, ctx, row, answer):
    await db.execute(
        text(
            "UPDATE forge.assistant_turns SET state='COMPLETED',lease_until=NULL,"
            "response=jsonb_set(coalesce(response,'{}'::jsonb),'{answer}',CAST(:answer AS "
            "jsonb),true) "
            "WHERE organization_id=:org AND owner_id=:owner AND id=:turn AND state='WAITING'"
        ),
        params(ctx, turn=row["turn_id"], answer=json.dumps(answer)),
    )


async def approve_proposal(db, ctx, id: UUID, body: ProposalApproval, key: str) -> ProposalRead:
    _key(key)
    row = await _locked_proposal(db, ctx, id, require_current=False)
    # Revalidate business authority even when the permanent receipt already exists.
    if row["kind"] == "SALES":
        drafts.sales_orders.require(ctx, "sales.order.write")
    else:
        drafts.purchase_orders.require(ctx, "purchase.order.write")
    previous = await _receipt(db, ctx, id)
    if previous:
        if (
            previous["revision"] != body.expected_revision
            or previous["confirmation_hash"] != body.confirmation_hash
        ):
            raise Problem(409, "AI_PROPOSAL_CHANGED", "批准内容与已创建草稿不一致")
        return ProposalRead(
            id=id,
            turn_id=row["turn_id"],
            kind=row["kind"],
            revision=previous["revision"],
            status="CREATED",
            expires_at=row["expires_at"],
            preview=None,
            receipt=DraftCreationReceipt.model_validate(previous["receipt"]),
        )
    await require_conversation(db, ctx, row["conversation_id"])
    _pending(row, body.expected_revision)
    await _review_ready(db, ctx, row)
    if row["confirmation_hash"] != body.confirmation_hash:
        raise Problem(409, "AI_PROPOSAL_CHANGED", "请批准当前已复核的提案")
    draft = DRAFT_INPUT.validate_python(row["body"])
    receipt = await drafts.create_draft(
        db, ctx, draft, body.confirmation_hash, "ai-proposal-" + id.hex
    )
    public = DraftCreationReceipt(
        **receipt,
        proposal_id=id,
        href=("/sales?order=" if row["kind"] == "SALES" else "/purchase?order=") + receipt["id"],
    )
    await db.execute(
        text(
            "INSERT INTO forge.assistant_draft_receipts "
            "(organization_id,owner_id,conversation_id,turn_id,proposal_id,revision,confirmatio"
            "n_hash,kind,"
            "sales_order_id,purchase_order_id,receipt,request_id) "
            "VALUES(:org,:owner,:conversation,:turn,:id,:revision,:hash,:kind,:sales,:purchase,"
            "CAST(:receipt AS jsonb),:rid)"
        ),
        params(
            ctx,
            conversation=row["conversation_id"],
            turn=row["turn_id"],
            id=id,
            revision=row["revision"],
            hash=row["confirmation_hash"],
            kind=row["kind"],
            sales=UUID(receipt["id"]) if row["kind"] == "SALES" else None,
            purchase=UUID(receipt["id"]) if row["kind"] == "PURCHASE" else None,
            receipt=public.model_dump_json(),
            rid=ctx.request_id,
        ),
    )
    await db.execute(
        text(
            "UPDATE forge.assistant_proposals SET status='CREATED' "
            "WHERE organization_id=:org AND owner_id=:owner AND id=:id"
        ),
        params(ctx, id=id),
    )
    await _audit(
        db,
        ctx,
        "ai.proposal.approved",
        "assistant_proposal",
        id,
        {
            "version": row["revision"],
            "conversation_id": str(row["conversation_id"]),
            "turn_id": str(row["turn_id"]),
            "kind": row["kind"],
            "document_id": receipt["id"],
        },
    )
    await _complete_waiting(db, ctx, row, "已按复核内容创建草稿，请在原单工作台继续办理。")
    return await proposal_detail(db, ctx, id)


async def reject_proposal(db, ctx, id: UUID, body: ProposalRejection, key: str) -> ProposalRead:
    row = await _locked_proposal(db, ctx, id)

    async def reject():
        _pending(row, body.expected_revision)
        await _review_ready(db, ctx, row)
        await db.execute(
            text(
                "UPDATE forge.assistant_proposals SET status='REJECTED' "
                "WHERE organization_id=:org AND owner_id=:owner AND id=:id"
            ),
            params(ctx, id=id),
        )
        await _audit(
            db,
            ctx,
            "ai.proposal.rejected",
            "assistant_proposal",
            id,
            {
                "version": row["revision"],
                "conversation_id": str(row["conversation_id"]),
                "turn_id": str(row["turn_id"]),
            },
        )
        await _complete_waiting(db, ctx, row, "已拒绝本次提案，未创建业务草稿。")
        return {"id": str(id), "status": "REJECTED"}

    await execute_once(
        db, ctx, f"ai.proposal.reject:{id}", key, body.model_dump(mode="json"), reject
    )
    return await proposal_detail(db, ctx, id)
