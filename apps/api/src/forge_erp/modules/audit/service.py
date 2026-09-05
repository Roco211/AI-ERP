import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.context import RuntimeContext


async def record_event(
    db: AsyncSession,
    ctx: RuntimeContext,
    action: str,
    resource_id: UUID,
) -> None:
    # Only allowlisted metadata; no credential/request-body serialization.
    params = {
        "org": ctx.organization_id,
        "actor": ctx.user_id,
        "action": action,
        "id": resource_id,
        "rid": ctx.request_id,
        "source": ctx.source,
    }
    await db.execute(
        text(
            "INSERT INTO forge.audit_events (organization_id,actor_type,actor_id,action,"
            "resource_type,resource_id,request_id,source,after) VALUES "
            "(:org,'USER',:actor,:action,'session',:id,:rid,:source,CAST(:after AS jsonb))"
        ),
        {**params, "after": json.dumps({"session_id": str(resource_id)})},
    )
    await db.execute(
        text(
            "INSERT INTO forge.outbox_events (organization_id,event_type,payload,request_id) "
            "VALUES (:org,:action,CAST(:payload AS jsonb),:rid)"
        ),
        {**params, "payload": json.dumps({"session_id": str(resource_id)})},
    )
