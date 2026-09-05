"""Erase expired assistant content while preserving permanent business receipts."""

import json
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge_erp.core.context import RuntimeContext
from forge_erp.core.errors import Problem

_SCOPE = "organization_id=:org AND owner_id=:owner AND conversation_id=:conversation"
_MAX_SCOPE_ROWS = 100
_MAX_SCOPES = 20


def _limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_SCOPE_ROWS:
        raise ValueError("Assistant retention limit must be between 1 and 100")
    return value


async def _record(db: AsyncSession, ctx: RuntimeContext, conversation: UUID, result: dict) -> None:
    metadata = {
        "version": 1,
        "purged_turns": result["turns"],
        "purged_proposals": result["proposals"],
        "conversation_purged": bool(result["conversations"]),
    }
    params = {
        "org": ctx.organization_id,
        "conversation": conversation,
        "rid": ctx.request_id,
        "after": json.dumps(metadata),
    }
    # Owner identity scopes RLS; the system, not that user, performs this erasure.
    await db.execute(
        text(
            "INSERT INTO forge.audit_events "
            "(organization_id,actor_type,actor_id,action,resource_type,resource_id,"
            "request_id,source,after) VALUES (:org,'SYSTEM',NULL,'ai.retention.purged',"
            "'assistant_conversation',:conversation,:rid,'SYSTEM',CAST(:after AS jsonb))"
        ),
        params,
    )
    await db.execute(
        text(
            "INSERT INTO forge.outbox_events(organization_id,event_type,payload,request_id) "
            "VALUES(:org,'ai.retention.purged',CAST(:payload AS jsonb),:rid)"
        ),
        {
            **params,
            "payload": json.dumps({"resource_id": str(conversation), "version": 1}),
        },
    )


async def purge_scope(
    factory: async_sessionmaker[AsyncSession],
    context: RuntimeContext,
    conversation_id: UUID,
    limit: int = 100,
) -> dict[str, int]:
    """One short transaction, at most `limit` turns and `limit` proposals.

    Checkpoints/writes have enforced per-turn bounds in the saver. Row ownership
    and current expiration are rechecked while holding the conversation lock.
    Expiration maintenance also runs for disabled users and organizations; it
    invokes no business Query/Command and never resurrects their permissions.
    """
    _limit(limit)
    if context.source != "SYSTEM":
        raise Problem(403, "PERMISSION_DENIED", "System retention context required")
    params = {
        "org": context.organization_id,
        "owner": context.user_id,
        "conversation": conversation_id,
        "limit": limit,
    }
    result = {"conversations": 0, "turns": 0, "proposals": 0}
    async with factory.begin() as db:
        await db.execute(
            text(
                "SELECT set_config('app.organization_id',:org,true),"
                "set_config('app.user_id',:owner,true)"
            ),
            {"org": str(context.organization_id), "owner": str(context.user_id)},
        )
        parent = (
            (
                await db.execute(
                    text(
                        "SELECT deleted_at IS NOT NULL OR expires_at<=clock_timestamp() AS expired "
                        "FROM forge.assistant_conversations WHERE organization_id=:org "
                        "AND owner_id=:owner AND id=:conversation AND body_purged_at IS NULL "
                        "FOR UPDATE SKIP LOCKED"
                    ),
                    params,
                )
            )
            .mappings()
            .first()
        )
        if parent is None:
            return result
        params["parent_expired"] = parent["expired"]
        # Take child locks only after the parent, matching runtime review/delete.
        turns = (
            (
                await db.execute(
                    text(
                        "SELECT id FROM forge.assistant_turns WHERE "
                        + _SCOPE
                        + " AND body_purged_at IS NULL "
                        "AND (:parent_expired OR expires_at<=clock_timestamp()) "
                        "ORDER BY expires_at,id LIMIT :limit FOR UPDATE SKIP LOCKED"
                    ),
                    params,
                )
            )
            .scalars()
            .all()
        )
        if turns:
            turn_params = {**params, "ids": [str(turn) for turn in turns]}
            for table in ("assistant_checkpoint_writes", "assistant_checkpoints"):
                await db.execute(
                    text(
                        f"DELETE FROM forge.{table} WHERE "
                        + _SCOPE
                        + " AND turn_id=ANY(CAST(:ids AS uuid[]))"
                    ),
                    turn_params,
                )
            await db.execute(
                text(
                    "UPDATE forge.assistant_turns SET prompt='',request_body='{}'::jsonb,"
                    "response=NULL,lease_until=NULL,body_purged_at=clock_timestamp(),"
                    "error_code=CASE WHEN state IN ('RUNNING','WAITING') "
                    "THEN 'AI_STATE_EXPIRED' ELSE error_code END,"
                    "state=CASE WHEN state IN ('RUNNING','WAITING') THEN 'FAILED' ELSE state END "
                    "WHERE " + _SCOPE + " AND id=ANY(CAST(:ids AS uuid[]))"
                ),
                turn_params,
            )
            result["turns"] = len(turns)
        # This separate bounded lane also repairs markers after explicit deletion
        # and advances when earlier batches already erased their parent turns.
        proposals = (
            (
                await db.execute(
                    text(
                        "SELECT p.id FROM forge.assistant_proposals p JOIN forge.assistant_turns t "
                        "ON t.organization_id=p.organization_id AND t.owner_id=p.owner_id "
                        "AND t.conversation_id=p.conversation_id AND t.id=p.turn_id "
                        "WHERE p.organization_id=:org AND p.owner_id=:owner "
                        "AND p.conversation_id=:conversation AND p.body_purged_at IS NULL "
                        "AND (:parent_expired OR t.expires_at<=clock_timestamp()) "
                        "ORDER BY t.expires_at,p.id LIMIT :limit FOR UPDATE OF p SKIP LOCKED"
                    ),
                    params,
                )
            )
            .scalars()
            .all()
        )
        if proposals:
            await db.execute(
                text(
                    "UPDATE forge.assistant_proposals SET body='{}'::jsonb,preview='{}'::jsonb,"
                    "status=CASE WHEN status='PENDING' THEN 'EXPIRED' ELSE status END,"
                    "body_purged_at=clock_timestamp() WHERE "
                    + _SCOPE
                    + " AND id=ANY(CAST(:ids AS uuid[]))"
                ),
                {**params, "ids": [str(proposal) for proposal in proposals]},
            )
            result["proposals"] = len(proposals)
        if parent["expired"]:
            done = (
                await db.execute(
                    text(
                        "SELECT NOT EXISTS(SELECT 1 FROM forge.assistant_turns WHERE "
                        + _SCOPE
                        + " AND body_purged_at IS NULL) AND NOT EXISTS(SELECT 1 "
                        "FROM forge.assistant_proposals WHERE "
                        + _SCOPE
                        + " AND body_purged_at IS NULL)"
                    ),
                    params,
                )
            ).scalar_one()
            if done:
                await db.execute(
                    text(
                        "UPDATE forge.assistant_conversations SET title='已清理的对话',"
                        "body_purged_at=clock_timestamp() WHERE organization_id=:org "
                        "AND owner_id=:owner AND id=:conversation"
                    ),
                    params,
                )
                result["conversations"] = 1
        await db.execute(
            text(
                "UPDATE forge.assistant_conversations SET retention_checked_at=clock_timestamp() "
                "WHERE organization_id=:org AND owner_id=:owner AND id=:conversation"
            ),
            params,
        )
        if any(result.values()):
            await _record(db, context, conversation_id, result)
    return result


async def drain(
    factory: async_sessionmaker[AsyncSession],
    organization_id: UUID | None = None,
    limit: int = 100,
) -> dict[str, int]:
    """Bounded discovery, followed by ordinary owner-scoped erasure transactions."""
    _limit(limit)
    async with factory.begin() as db:
        role = (
            await db.execute(
                text(
                    "SELECT rolname,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole "
                    "FROM pg_roles WHERE rolname=current_user"
                )
            )
        ).one()
        if role[0] != "forge_app" or any(role[1:]):
            raise RuntimeError("Assistant retention requires restricted forge_app")
        scopes = (
            await db.execute(
                text("SELECT * FROM forge.pending_assistant_retention(:limit,:org)"),
                {"limit": min(limit, _MAX_SCOPES), "org": organization_id},
            )
        ).all()
    total = {"conversations": 0, "turns": 0, "proposals": 0}
    per_scope = max(1, limit // max(1, len(scopes)))
    for org, owner, conversation in scopes:
        context = RuntimeContext(org, owner, frozenset(), "ai-retention-" + uuid4().hex, "SYSTEM")
        result = await purge_scope(factory, context, conversation, per_scope)
        for key in total:
            total[key] += result[key]
    return total
