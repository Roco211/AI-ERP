import asyncio
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.modules.catalog.infrastructure import embeddings

log = structlog.get_logger()


async def index_product(db: AsyncSession, organization_id: UUID, product_id: UUID) -> bool:
    """Tenant-scoped projection refresh; no catalog facts are modified."""
    if not settings().embedding_enabled:
        return False
    row = (
        (
            await db.execute(
                text(
                    "SELECT version,search_text,active FROM forge.products "
                    "WHERE organization_id=:org AND id=:id"
                ),
                {"org": organization_id, "id": product_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None or not row["active"]:
        return False
    current = (
        await db.execute(
            text(
                "SELECT 1 FROM forge.product_embeddings "
                "WHERE organization_id=:org AND product_id=:id AND product_version=:version "
                "AND model_identity=:model"
            ),
            {
                "org": organization_id,
                "id": product_id,
                "version": row["version"],
                "model": embeddings.model_identity(),
            },
        )
    ).first()
    if current:
        return False
    vector = await embeddings.embed(row["search_text"], indexing=True)
    # A concurrent edit must not publish a vector for an obsolete product version.
    result = await db.execute(
        text("""INSERT INTO forge.product_embeddings
        (organization_id,product_id,product_version,model_identity,embedding)
        SELECT organization_id,id,version,:model,CAST(:vector AS vector) FROM forge.products
        WHERE organization_id=:org AND id=:id AND version=:version AND active
        ON CONFLICT(organization_id,product_id) DO UPDATE SET
            product_version=EXCLUDED.product_version,model_identity=EXCLUDED.model_identity,
            embedding=EXCLUDED.embedding,updated_at=now()
        WHERE forge.product_embeddings.product_version<=EXCLUDED.product_version
        RETURNING product_id"""),
        {
            "org": organization_id,
            "id": product_id,
            "version": row["version"],
            "model": embeddings.model_identity(),
            "vector": vector,
        },
    )
    return result.first() is not None


async def rebuild(db: AsyncSession, ctx: RuntimeContext, limit: int = 100) -> int:
    ctx.require("catalog.write")
    ctx.require("catalog.read")
    ids = (
        (
            await db.execute(
                text("""SELECT p.id FROM forge.products p
        LEFT JOIN forge.product_embeddings e
          ON e.organization_id=p.organization_id AND e.product_id=p.id
        WHERE p.organization_id=:org AND p.active
          AND (e.product_id IS NULL OR e.product_version<>p.version OR e.model_identity<>:model)
        ORDER BY p.id LIMIT :limit"""),
                {"org": ctx.organization_id, "model": embeddings.model_identity(), "limit": limit},
            )
        )
        .scalars()
        .all()
    )
    done = 0
    for product_id in ids:
        done += await index_product(db, ctx.organization_id, product_id)
    return done


async def semantic_ids(
    db: AsyncSession, ctx: RuntimeContext, q: str, base_clauses: list[str], params: dict
) -> list[UUID]:
    if not settings().embedding_enabled:
        return []
    try:
        # Total request bound includes model inspection and inference.
        async with asyncio.timeout(2.5):
            vector = await embeddings.embed(q)
    except embeddings.EmbeddingUnavailable, TimeoutError:
        log.warning("semantic_search_unavailable", request_id=ctx.request_id)
        return []
    rows = await db.execute(
        text(f"""SELECT p.id FROM forge.products p
        JOIN forge.product_embeddings e ON e.organization_id=p.organization_id AND e.product_id=p.id
        WHERE {" AND ".join(base_clauses)} AND p.active AND e.product_version=p.version
          AND e.model_identity=:model AND 1-(e.embedding <=> CAST(:vector AS vector))>=:threshold
        ORDER BY e.embedding <=> CAST(:vector AS vector),p.id LIMIT 20"""),
        params
        | {
            "model": embeddings.model_identity(),
            "vector": vector,
            "threshold": settings().embedding_min_similarity,
        },
    )
    return list(rows.scalars())
