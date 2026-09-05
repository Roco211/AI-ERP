from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions
from forge_erp.core.errors import Problem
from forge_erp.core.idempotency import execute_once
from forge_erp.modules.assistant.domain.providers import (
    ProviderConnection,
    ProviderInput,
    ProviderRead,
    ProviderTestRead,
)
from forge_erp.modules.assistant.infrastructure.credentials import decrypt_key, encrypt_key
from forge_erp.modules.assistant.infrastructure.provider import request_decision
from forge_erp.modules.audit.service import record_mutation
from forge_erp.modules.identity.application.commands import resolve_context


async def row(db: AsyncSession, ctx: RuntimeContext, *, lock: bool = False):
    return (
        (
            await db.execute(
                text(
                    "SELECT * FROM forge.ai_provider_settings WHERE organization_id=:org"
                    + (" FOR UPDATE" if lock else "")
                ),
                {"org": ctx.organization_id},
            )
        )
        .mappings()
        .first()
    )


def public(value) -> ProviderRead:
    if value is None:
        return ProviderRead()
    return ProviderRead(
        **{
            k: value[k]
            for k in (
                "name",
                "base_url",
                "model",
                "enabled",
                "allow_private_network",
                "version",
                "updated_at",
            )
        },
        key_set=bool(value["encrypted_key"]),
    )


async def read(db: AsyncSession, ctx: RuntimeContext) -> ProviderRead:
    ctx.require("ai.provider.manage")
    return public(await row(db, ctx))


async def version(db: AsyncSession, ctx: RuntimeContext) -> int:
    result = await row(db, ctx)
    return result["version"] if result else 0


def connection_for(value, org: UUID) -> ProviderConnection:
    if value is None or not value["enabled"]:
        raise Problem(503, "AI_NOT_CONFIGURED", "请先在设置中配置并启用模型服务")
    return ProviderConnection(
        base_url=value["base_url"],
        model=value["model"],
        key=decrypt_key(org, value["encrypted_key"]),
        allow_private_network=value["allow_private_network"],
        version=value["version"],
    )


async def connection(db: AsyncSession, ctx: RuntimeContext) -> ProviderConnection:
    ctx.require("ai.use")
    return connection_for(await row(db, ctx), ctx.organization_id)


async def save(
    db: AsyncSession, ctx: RuntimeContext, body: ProviderInput, key: str
) -> ProviderRead:
    ctx.require("ai.provider.manage")
    payload = body.model_dump(mode="json", exclude={"api_key"})
    payload["api_key"] = body.api_key.get_secret_value() if body.api_key is not None else None

    async def execute():
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock,0))"),
            {"lock": f"ai-provider:{ctx.organization_id}"},
        )
        previous = await row(db, ctx, lock=True)
        old_version = previous["version"] if previous else 0
        if old_version != body.expected_version:
            raise Problem(409, "AI_PROVIDER_CHANGED", "模型设置已被修改，请重新读取后保存")
        encrypted = previous["encrypted_key"] if previous else None
        if (
            encrypted
            and previous
            and body.api_key is None
            and not body.clear_key
            and (urlsplit(previous["base_url"]).scheme, urlsplit(previous["base_url"]).netloc)
            != (urlsplit(body.base_url).scheme, urlsplit(body.base_url).netloc)
        ):
            raise Problem(422, "AI_PROVIDER_KEY_REQUIRED", "切换服务地址时请重新填写或清除密钥")
        if body.clear_key:
            encrypted = None
        elif body.api_key is not None:
            encrypted = encrypt_key(ctx.organization_id, body.api_key)
        saved = (
            (
                await db.execute(
                    text("""
 INSERT INTO forge.ai_provider_settings
 (organization_id,name,base_url,model,enabled,allow_private_network,encrypted_key,version,updated_by)
 VALUES(:org,:name,:url,:model,:enabled,:private,:encrypted,:version,:actor)
 ON CONFLICT(organization_id) DO UPDATE SET
 name=EXCLUDED.name,base_url=EXCLUDED.base_url,model=EXCLUDED.model,enabled=EXCLUDED.enabled,
 allow_private_network=EXCLUDED.allow_private_network,encrypted_key=EXCLUDED.encrypted_key,
 version=EXCLUDED.version,updated_by=EXCLUDED.updated_by,updated_at=clock_timestamp(),
 test_started_at=NULL RETURNING *
 """),
                    {
                        "org": ctx.organization_id,
                        "actor": ctx.user_id,
                        "name": body.name,
                        "url": body.base_url,
                        "model": body.model,
                        "enabled": body.enabled,
                        "private": body.allow_private_network,
                        "encrypted": encrypted,
                        "version": old_version + 1,
                    },
                )
            )
            .mappings()
            .one()
        )
        await record_mutation(
            db,
            ctx,
            "ai.provider.updated",
            "ai_provider",
            ctx.organization_id,
            None,
            {
                "version": old_version + 1,
                "key_changed": body.clear_key or body.api_key is not None,
                "enabled": body.enabled,
            },
        )
        return public(saved).model_dump(mode="json")

    response = await execute_once(db, ctx, "ai.provider.save", key, payload, execute)
    return ProviderRead.model_validate(response)


async def test_connection(token: str, request_id: str, expected_version: int, key: str):
    if not 8 <= len(key) <= 128:
        raise Problem(422, "INVALID_IDEMPOTENCY_KEY", "Key must contain 8–128 characters")
    async with sessions.begin() as db:
        ctx = await resolve_context(db, token, request_id)
        ctx.require("ai.provider.manage")
        value = await row(db, ctx, lock=True)
        if not value or value["version"] != expected_version:
            raise Problem(409, "AI_PROVIDER_CHANGED", "模型设置已变化，请重新读取后测试")
        # Only previous complete results are reusable; no raw credential in receipts.
        previous = (
            (
                await db.execute(
                    text("""
 SELECT request_hash,response FROM forge.idempotency_keys WHERE organization_id=:org
 AND actor_id=:actor AND operation='ai.provider.test' AND key=:key AND expires_at>now()
 """),
                    {"org": ctx.organization_id, "actor": ctx.user_id, "key": key},
                )
            )
            .mappings()
            .first()
        )
        from forge_erp.core.security import fingerprint

        request_hash = fingerprint({"version": expected_version})
        if previous:
            if previous["request_hash"] != request_hash:
                raise Problem(409, "IDEMPOTENCY_KEY_REUSED", "此键已用于另一连接测试")
            return ProviderTestRead.model_validate(previous["response"])
        last = value["test_started_at"]
        if last and (datetime.now(UTC) - last).total_seconds() < 30:
            raise Problem(429, "AI_PROVIDER_TEST_BUSY", "连接测试正在运行或刚结束，请稍后再试")
        conn = connection_for(value, ctx.organization_id)
        await db.execute(
            text(
                "UPDATE forge.ai_provider_settings SET test_started_at=clock_timestamp() "
                "WHERE organization_id=:org"
            ),
            {"org": ctx.organization_id},
        )
    # Synthetic text only; no ERP data or open database transaction during network wait.
    decision = await request_decision(
        [
            SystemMessage(content='Return only the JSON object {"ok":true}. Do not call tools.'),
            HumanMessage(content="这是一条连接测试，请返回约定的 JSON。"),
        ],
        conn,
    )
    if set(decision) != {"ok"} or decision["ok"] is not True:
        raise Problem(503, "AI_PROTOCOL_UNSUPPORTED", "连接成功，但模型未遵循决策格式，请检查模型")
    async with sessions.begin() as db:
        current = await resolve_context(db, token, request_id)
        current.require("ai.provider.manage")
        if (current.organization_id, current.user_id) != (ctx.organization_id, ctx.user_id):
            raise Problem(403, "PERMISSION_DENIED", "Permission denied")
        value = await row(db, current, lock=True)
        if not value or value["version"] != expected_version:
            raise Problem(409, "AI_PROVIDER_CHANGED", "测试期间设置已修改，请测试最新配置")

        async def record():
            result = ProviderTestRead(
                ok=True,
                message="连接成功，模型可返回助手需要的决策格式。",
                model=conn.model,
                version=conn.version,
            )
            await record_mutation(
                db,
                current,
                "ai.provider.tested",
                "ai_provider",
                ctx.organization_id,
                None,
                {"version": conn.version, "ok": True},
            )
            return result.model_dump(mode="json")

        return ProviderTestRead.model_validate(
            await execute_once(
                db, current, "ai.provider.test", key, {"version": expected_version}, record
            )
        )
