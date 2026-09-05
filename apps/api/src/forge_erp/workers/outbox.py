import asyncio

import structlog
from sqlalchemy import text

from forge_erp.core.db import engine, sessions, set_tenant, verify_database_role
from forge_erp.workers.celery_app import celery_app

KNOWN_EVENTS = {"identity.session.created", "identity.session.revoked"}
log = structlog.get_logger()


async def drain_outbox() -> int:
    await verify_database_role()
    processed = 0
    async with sessions.begin() as db:
        tenants = (await db.execute(text("SELECT * FROM forge.pending_outbox_tenants()"))).all()
    for (org,) in tenants:
        async with sessions.begin() as db:
            await set_tenant(db, org)
            rows = (
                (
                    await db.execute(
                        text(
                            "SELECT id,event_type,request_id FROM forge.outbox_events "
                            "WHERE organization_id=:org AND processed_at IS NULL "
                            "ORDER BY created_at,id LIMIT 100 FOR UPDATE SKIP LOCKED"
                        ),
                        {"org": org},
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                if row["event_type"] not in KNOWN_EVENTS:
                    log.warning("unknown_outbox_event", event_id=str(row["id"]))
                    continue
                log.info(
                    "identity_event_processed",
                    event_id=str(row["id"]),
                    organization_id=str(org),
                    request_id=row["request_id"],
                )
                await db.execute(
                    text(
                        "UPDATE forge.outbox_events SET processed_at=now(),"
                        "attempts=attempts+1 WHERE organization_id=:org AND id=:id"
                    ),
                    {"org": org, "id": row["id"]},
                )
                processed += 1
    return processed


@celery_app.task(name="forge.outbox.poll")
def poll_outbox() -> int:
    async def run() -> int:
        try:
            return await drain_outbox()
        finally:
            await engine.dispose()

    return asyncio.run(run())
