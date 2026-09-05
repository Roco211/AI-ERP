import asyncio
from uuid import UUID

import structlog
from sqlalchemy import text

from forge_erp.core.config import settings
from forge_erp.core.db import engine, sessions, set_tenant, verify_database_role
from forge_erp.modules.catalog.application.semantic import index_product
from forge_erp.modules.catalog.infrastructure.embeddings import EmbeddingUnavailable
from forge_erp.modules.catalog.infrastructure.resources import RESOURCES
from forge_erp.workers.celery_app import celery_app

KNOWN_EVENTS = {"identity.session.created", "identity.session.revoked"}
# Product events also refresh the local vector projection when enabled.

KNOWN_EVENTS |= {
    f"catalog.{resource}.{action}"
    for resource in RESOURCES
    for action in ("create", "update", "activate", "deactivate")
}
KNOWN_EVENTS |= {"inventory.movement.recorded", "inventory.projection.rebuilt"}
KNOWN_EVENTS |= {
    f"inventory.document.{action}" for action in ("create", "update", "post", "reverse", "refresh")
}
KNOWN_EVENTS |= {
    f"purchase.order.{action}" for action in ("create", "update", "confirm", "cancel", "close")
}
KNOWN_EVENTS |= {
    f"purchase.document.{action}" for action in ("create", "update", "post", "reverse")
}
KNOWN_EVENTS |= {
    f"sales.order.{action}" for action in ("create", "update", "confirm", "cancel", "close")
}
KNOWN_EVENTS |= {f"sales.document.{action}" for action in ("create", "update", "post", "reverse")}
KNOWN_EVENTS |= {
    "funds.activate",
    "funds.opening",
    "funds.adjustment",
    "funds.legacy.bind",
    "funds.cash.post",
    "funds.cash.reverse",
    "funds.source.reverse",
    "funds.source.post",
    "funds.source.return",
    "funds.source.document.reverse",
}
KNOWN_EVENTS |= {
    "catalog.import.preview",
    "catalog.import.confirm",
    "catalog.import.retry",
    "catalog.import.row.succeeded",
    "catalog.import.expired",
    "replenishment.purchase.created",
}
log = structlog.get_logger()


async def drain_outbox(organization_id: UUID | None = None) -> int:
    await verify_database_role()
    processed = 0
    async with sessions.begin() as db:
        tenants = (await db.execute(text("SELECT * FROM forge.pending_outbox_tenants()"))).all()
    for (org,) in tenants:
        if organization_id is not None and org != organization_id:
            continue
        async with sessions.begin() as db:
            await set_tenant(db, org)
            # Ordinary committed events must advance even if local embeddings
            # are disabled or unavailable. Bound and lock each lane separately.
            rows = list(
                (
                    await db.execute(
                        text(
                            "SELECT id,event_type,request_id,payload FROM forge.outbox_events "
                            "WHERE organization_id=:org AND processed_at IS NULL "
                            "AND event_type NOT LIKE 'catalog.products.%' "
                            "ORDER BY created_at,id LIMIT 100 FOR UPDATE SKIP LOCKED"
                        ),
                        {"org": org},
                    )
                )
                .mappings()
                .all()
            )
            if settings().embedding_enabled:
                rows.extend(
                    (
                        await db.execute(
                            text(
                                "SELECT id,event_type,request_id,payload FROM forge.outbox_events "
                                "WHERE organization_id=:org AND processed_at IS NULL "
                                "AND event_type LIKE 'catalog.products.%' "
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
                if row["event_type"].startswith("catalog.products."):
                    if not settings().embedding_enabled:
                        continue  # Keep pending for a later enabled worker.
                    try:
                        await index_product(db, org, UUID(row["payload"]["resource_id"]))
                    except EmbeddingUnavailable:
                        await db.execute(
                            text(
                                "UPDATE forge.outbox_events SET attempts=attempts+1 "
                                "WHERE organization_id=:org AND id=:id"
                            ),
                            {"org": org, "id": row["id"]},
                        )
                        log.warning(
                            "product_embedding_retry",
                            event_id=str(row["id"]),
                            request_id=row["request_id"],
                        )
                        break  # Retry on the next scheduled poll; do not hammer the model.
                log.info(
                    "outbox_event_processed",
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
