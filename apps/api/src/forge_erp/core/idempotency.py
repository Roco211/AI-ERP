import json
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.context import RuntimeContext
from forge_erp.core.errors import Problem
from forge_erp.core.security import fingerprint


async def execute_once(
    db: AsyncSession,
    ctx: RuntimeContext,
    operation: str,
    key: str,
    body: object,
    command: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    if not 8 <= len(key) <= 128:
        raise Problem(422, "INVALID_IDEMPOTENCY_KEY", "Key must contain 8–128 characters")
    params = {"org": ctx.organization_id, "actor": ctx.user_id, "op": operation, "key": key}
    lock = f"{ctx.organization_id}:{ctx.user_id}:{operation}:{key}"
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock, 0))"), {"lock": lock}
    )
    await db.execute(
        text(
            "DELETE FROM forge.idempotency_keys WHERE organization_id=:org "
            "AND actor_id=:actor AND operation=:op AND key=:key "
            "AND expires_at <= now()"
        ),
        params,
    )
    previous = (
        (
            await db.execute(
                text(
                    "SELECT request_hash,response FROM forge.idempotency_keys "
                    "WHERE organization_id=:org "
                    "AND actor_id=:actor AND operation=:op AND key=:key"
                ),
                params,
            )
        )
        .mappings()
        .first()
    )
    request_hash = fingerprint(body)
    if previous:
        if previous["request_hash"] != request_hash:
            raise Problem(409, "IDEMPOTENCY_KEY_REUSED", "Key was used for a different request")
        return dict(previous["response"])
    result = await command()
    await db.execute(
        text(
            "INSERT INTO forge.idempotency_keys "
            "(organization_id,actor_id,operation,key,request_hash,response) "
            "VALUES (:org,:actor,:op,:key,:hash,CAST(:response AS jsonb))"
        ),
        {**params, "hash": request_hash, "response": json.dumps(result, default=str)},
    )
    return result
