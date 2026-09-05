from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from forge_erp.core.config import Settings, settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions, set_tenant
from forge_erp.modules.catalog.application.search import search_products
from forge_erp.modules.catalog.application.semantic import index_product
from forge_erp.modules.catalog.application.service import write_command
from forge_erp.modules.catalog.infrastructure import embeddings
from forge_erp.workers.outbox import drain_outbox

# These are deliberate test fixtures, not real model output. Live eval is separate.
VECTOR = embeddings.vector_literal([1.0] + [0.0] * 1023)


@pytest.mark.parametrize(
    "value", [[], [1.0] * 1023, [float("nan")] * 1024, [0.0] * 1024, [True] * 1024]
)
def test_reject_invalid_model_vectors(value):
    with pytest.raises(embeddings.EmbeddingUnavailable):
        embeddings.vector_literal(value)


def test_local_only_settings():
    with pytest.raises(ValueError, match="local Ollama"):
        Settings(embedding_url="https://api.example.test", embedding_enabled=False)
    with pytest.raises(ValueError, match="pinned"):
        Settings(embedding_enabled=True, embedding_model_digest="")


async def setup_product(identity):
    ctx = RuntimeContext(
        identity["org"],
        identity["user"],
        frozenset({"catalog.read", "catalog.write"}),
        "embedding-test",
    )
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        unit = await write_command(db, ctx, "units", {"code": "PCS", "name": "个"}, uuid4().hex)
        cat = await write_command(
            db, ctx, "categories", {"code": "TOOLS", "name": "工具"}, uuid4().hex
        )
        product = await write_command(
            db,
            ctx,
            "products",
            {
                "sku": "SPANNER",
                "name": "活动扳手",
                "category_id": cat["id"],
                "base_unit_id": unit["id"],
            },
            uuid4().hex,
        )
    return ctx, UUID(product["id"])


async def test_semantic_tenancy_staleness_filters_and_exact_priority(identities, monkeypatch):
    a, product = await setup_product(identities[0])
    b, other = await setup_product(identities[1])
    monkeypatch.setattr(settings(), "embedding_enabled", True)
    calls = []

    async def fake(value, **kwargs):
        calls.append(value)
        return VECTOR

    monkeypatch.setattr(embeddings, "embed", fake)
    for ctx, pid in [(a, product), (b, other)]:
        async with sessions.begin() as db:
            await set_tenant(db, ctx.organization_id)
            assert await index_product(db, ctx.organization_id, pid)
    async with sessions.begin() as db:
        await set_tenant(db, a.organization_id)
        before = len(calls)
        exact = await search_products(db, a, "SPANNER")
        assert len(calls) == before  # Exact lookup never waits for the model.
        assert exact["items"][0]["id"] == product
        result = await search_products(db, a, "拧螺母的工具")
        assert [r["id"] for r in result["items"]] == [product]
        assert (await search_products(db, a, "拧螺母的工具", filters={"category_id": uuid4()}))[
            "total"
        ] == 0
        assert (await search_products(db, a, "拧螺母的工具", attributes={"材质": "不存在"}))[
            "total"
        ] == 0
        assert (
            await db.execute(text("SELECT count(*) FROM forge.product_embeddings"))
        ).scalar_one() == 1
        await db.execute(
            text("UPDATE forge.products SET version=version+1 WHERE id=:id"), {"id": product}
        )
        assert (await search_products(db, a, "拧螺母的工具"))["total"] == 0
        assert await index_product(db, a.organization_id, product)
        assert (await search_products(db, a, "拧螺母的工具"))["total"] == 1


async def test_model_failure_falls_back_and_outbox_retries(identities, monkeypatch):
    ctx, product = await setup_product(identities[0])
    monkeypatch.setattr(settings(), "embedding_enabled", True)

    async def fail(*args, **kwargs):
        raise embeddings.EmbeddingUnavailable("offline")

    monkeypatch.setattr(embeddings, "embed", fail)
    await drain_outbox(ctx.organization_id)
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        assert (await search_products(db, ctx, "活动扳手"))["total"] == 1
        assert (await search_products(db, ctx, "拧螺母的工具"))["total"] == 0
        pending = (
            await db.execute(
                text(
                    "SELECT processed_at,attempts FROM forge.outbox_events "
                    "WHERE event_type='catalog.products.create'"
                )
            )
        ).one()
        assert pending.processed_at is None and pending.attempts == 1

    async def restored(*args, **kwargs):
        return VECTOR

    monkeypatch.setattr(embeddings, "embed", restored)
    await drain_outbox(ctx.organization_id)
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        assert (
            await db.execute(
                text(
                    "SELECT processed_at FROM forge.outbox_events "
                    "WHERE event_type='catalog.products.create'"
                )
            )
        ).scalar_one() is not None
        assert (await search_products(db, ctx, "拧螺母的工具"))["items"][0]["id"] == product


async def test_concurrent_edit_cannot_publish_old_vector(identities, monkeypatch):
    ctx, product = await setup_product(identities[0])
    monkeypatch.setattr(settings(), "embedding_enabled", True)

    async def concurrent_edit(*args, **kwargs):
        async with sessions.begin() as writer:
            await set_tenant(writer, ctx.organization_id)
            await writer.execute(
                text("UPDATE forge.products SET version=version+1 WHERE id=:id"), {"id": product}
            )
        return VECTOR

    monkeypatch.setattr(embeddings, "embed", concurrent_edit)
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        assert not await index_product(db, ctx.organization_id, product)
        assert (
            await db.execute(text("SELECT count(*) FROM forge.product_embeddings"))
        ).scalar_one() == 0


async def test_adapter_checks_model_digest(monkeypatch):
    import httpx

    monkeypatch.setattr(settings(), "embedding_enabled", True)
    monkeypatch.setattr(settings(), "embedding_model_digest", "a" * 64)

    async def handle(request):
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": settings().embedding_model, "digest": "b" * 64}]}
            )
        pytest.fail("Must not infer using a different model artifact")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(embeddings.EmbeddingUnavailable, match="Pinned"):
        await embeddings.embed("商品")


async def test_model_identity_and_inactive_vectors_are_excluded(identities, monkeypatch):
    ctx, product = await setup_product(identities[0])
    monkeypatch.setattr(settings(), "embedding_enabled", True)

    async def fake(*args, **kwargs):
        return VECTOR

    monkeypatch.setattr(embeddings, "embed", fake)
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        await index_product(db, ctx.organization_id, product)
        monkeypatch.setattr(settings(), "embedding_model_digest", "different-model")
        assert (await search_products(db, ctx, "拧螺母的工具"))["total"] == 0
        await index_product(db, ctx.organization_id, product)
        assert (await search_products(db, ctx, "拧螺母的工具"))["total"] == 1
        await db.execute(
            text("UPDATE forge.products SET active=false WHERE id=:id"), {"id": product}
        )
        assert (await search_products(db, ctx, "拧螺母的工具", active=None))["total"] == 0


def test_semantic_description_excludes_identifier_and_price_noise():
    from forge_erp.modules.catalog.application.semantic import description

    row = {
        "sku": "PTFE-TAPE",
        "barcode": "69000123",
        "name": "生料带",
        "specification": "水管密封",
        "attributes": {"颜色": "白色"},
        "standard_price": "1.00",
    }
    assert description(row) == "生料带 水管密封 颜色: 白色"
    assert "catalog-v2" in embeddings.model_identity()
