import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_catalog import catalog_client as catalog_client
from test_catalog import create, product_fixture
from test_purchasing import action as purchase_action
from test_purchasing import detail as purchase_detail
from test_purchasing import po, receive, return_draft
from test_sales_orders import opening

from forge_erp.core import auth_dependencies
from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions, set_tenant
from forge_erp.modules.replenishment.application import commands, queries


@pytest.fixture
async def replenishment(catalog_client):
    c = catalog_client
    product, body, category, unit = await product_fixture(c)
    supplier = (await create(c, "suppliers", {"code": "RS", "name": "补货供应商"})).json()
    warehouse = (await create(c, "warehouses", {"code": "RW", "name": "补货收货仓"})).json()
    body |= {"min_stock_qty": "10", "reorder_qty": "20", "preferred_supplier_id": supplier["id"]}
    response = await c.put(
        "/api/v1/products/" + product["id"],
        json=body | {"expected_version": product["version"]},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 200, response.text
    sp = await create(
        c,
        "supplier-products",
        {
            "product_id": product["id"],
            "supplier_id": supplier["id"],
            "purchase_unit_id": unit["id"],
            "supplier_sku": "RS-BOLT",
            "lead_days": 5,
        },
    )
    assert sp.status_code == 201, sp.text
    return {
        "product": response.json(),
        "body": body,
        "unit": unit,
        "supplier": supplier,
        "warehouse": warehouse,
        "supplier_product": sp.json(),
        "category": category,
    }


async def advice(c, **params):
    r = await c.get("/api/v1/replenishment/suggestions", params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def preview_body(c, fixture, **line):
    item = (await advice(c))["items"][0]
    return {
        "supplier_id": fixture["supplier"]["id"],
        "warehouse_id": fixture["warehouse"]["id"],
        "reason": "已复核补货依据",
        "lines": [
            {
                "product_id": item["product_id"],
                "basis_hash": item["basis_hash"],
                "unit_id": fixture["unit"]["id"],
                "unit_price": "2.123456",
                **line,
            }
        ],
    }


async def preview(c, body):
    result = await c.post("/api/v1/replenishment/purchase-preview", json=body)
    assert result.status_code == 200, result.text
    return result.json()


async def confirmed_body(c, fixture, **line):
    body = await preview_body(c, fixture, **line)
    result = await preview(c, body)
    assert result["can_create"], result
    return body | {"confirmation_hash": result["confirmation_hash"]}, result


def purchase_body(fixture, qty="100", unit_id=None, price="10"):
    return {
        "supplier_id": fixture["supplier"]["id"],
        "warehouse_id": fixture["warehouse"]["id"],
        "reason": "补货统计采购",
        "lines": [
            {
                "product_id": fixture["product"]["id"],
                "unit_id": unit_id or fixture["unit"]["id"],
                "qty": qty,
                "unit_price": price,
            }
        ],
    }


async def fact_counts(identity):
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        return {
            table: (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar()
            for table in (
                "inventory_documents",
                "inventory_movements",
                "inventory_balances",
                "funds_sources",
                "funds_entries",
                "funds_cash_documents",
            )
        }


async def test_preview_creates_only_purchase_draft_and_permanent_provenance(
    catalog_client,
    replenishment,
    identities,
):
    c = catalog_client
    page = await advice(c)
    item = page["items"][0]
    assert item["lead_days"] == 5 and item["candidate"]
    assert [D(item[k]) for k in ("available_qty", "open_purchase_qty", "suggested_base_qty")] == [
        0,
        0,
        20,
    ]
    assert "NO_SALES_HISTORY" in item["reasons"] and "price" not in str(item)
    assert (await advice(c, page=9))["total"] == 1
    assert (await advice(c, page=9))["items"] == []
    assert (await advice(c, q="unknown"))["total"] == 0
    body, result = await confirmed_body(c, replenishment)
    assert D(result["lines"][0]["qty"]) == 20
    assert D(result["total_amount"]) == D("42.4691")
    assert (await preview(c, {k: v for k, v in body.items() if k != "confirmation_hash"}))[
        "confirmation_hash"
    ] == body["confirmation_hash"]
    before = await fact_counts(identities[0])
    response = await create(c, "replenishment/purchase-orders", body)
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt["status"] == "DRAFT"
    purchase = await purchase_detail(c, receipt)
    assert purchase["status"] == "DRAFT" and purchase["receiving_status"] == "UNRECEIVED"
    assert D(purchase["amount"]) == D(result["total_amount"])
    assert D(purchase["lines"][0]["base_qty"]) == 20
    assert before == await fact_counts(identities[0])
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        proof = (
            (await db.execute(text("SELECT * FROM forge.replenishment_creations"))).mappings().one()
        )
        assert str(proof["purchase_order_id"]) == receipt["id"]
        assert proof["confirmation_hash"] == body["confirmation_hash"]
        assert (
            proof["basis_snapshot"]["products"][0]["product_id"] == replenishment["product"]["id"]
        )
        assert proof["selection_snapshot"]["input"]["reason"] == body["reason"]
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.audit_events "
                    "WHERE action='replenishment.purchase.created'"
                )
            )
        ).scalar() == 1
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.outbox_events "
                    "WHERE event_type='replenishment.purchase.created'"
                )
            )
        ).scalar() == 1


async def test_receipts_reduce_inbound_returns_do_not_reopen_and_closed_orders_excluded(
    catalog_client,
    replenishment,
):
    c = catalog_client
    order = await po(c, purchase_body(replenishment))
    assert D((await advice(c))["items"][0]["open_purchase_qty"]) == 0
    confirmed = (await purchase_action(c, order)).json()
    received, _ = await receive(c, order, "25")
    assert (await purchase_action(c, received, "post", document=True)).status_code == 200
    ret, _ = await return_draft(c, received, "10")
    assert (await purchase_action(c, ret, "post", document=True)).status_code == 200
    item = (await advice(c))["items"][0]
    assert D(item["open_purchase_qty"]) == 75 and D(item["available_qty"]) == 15
    assert D(item["suggested_base_qty"]) == 0
    assert (await purchase_action(c, confirmed, "close", "不再收货")).status_code == 200
    assert D((await advice(c))["items"][0]["open_purchase_qty"]) == 0


async def test_same_key_concurrency_and_expired_cache_keep_one_draft(
    catalog_client,
    replenishment,
    identities,
):
    c = catalog_client
    body, _ = await confirmed_body(c, replenishment)
    key = uuid4().hex
    results = await asyncio.wait_for(
        asyncio.gather(*[create(c, "replenishment/purchase-orders", body, key) for _ in range(3)]),
        10,
    )
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    assert all(r.json() == results[0].json() for r in results)
    order = results[0].json()
    assert (await purchase_action(c, order, "cancel", "用户后来取消")).status_code == 200
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text("DELETE FROM forge.idempotency_keys WHERE organization_id=:org"), identities[0]
        )
    replay = await create(c, "replenishment/purchase-orders", body, key)
    assert replay.status_code == 201 and replay.json() == order
    changed = await create(c, "replenishment/purchase-orders", body | {"reason": "不同意图"}, key)
    assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        assert (await db.execute(text("SELECT count(*) FROM forge.purchase_orders"))).scalar() == 1
        assert (
            await db.execute(text("SELECT count(*) FROM forge.replenishment_creations"))
        ).scalar() == 1
    # A separately confirmed intent may create another DRAFT; cancelled history is retained.
    fresh, _ = await confirmed_body(c, replenishment)
    second = await create(c, "replenishment/purchase-orders", fresh)
    assert second.status_code == 201 and second.json()["id"] != order["id"]


@pytest.mark.parametrize("permission", queries.READ_PERMISSIONS)
async def test_all_underlying_permissions_required(
    catalog_client, replenishment, identities, permission
):
    c = catalog_client
    body, _ = await confirmed_body(c, replenishment)
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions "
                "WHERE organization_id=:org AND permission_code=:p"
            ),
            identities[0] | {"p": permission},
        )
    assert (await c.get("/api/v1/replenishment/suggestions")).status_code == 403
    assert (
        await c.post(
            "/api/v1/replenishment/purchase-preview",
            json={k: v for k, v in body.items() if k != "confirmation_hash"},
        )
    ).status_code == 403
    assert (await create(c, "replenishment/purchase-orders", body)).status_code == 403


@pytest.mark.parametrize(
    "permission", ("replenishment.create", "purchase.order.write", "product.cost.read")
)
async def test_creation_and_preview_recheck_permission_even_for_replay(
    catalog_client, replenishment, identities, permission
):
    c = catalog_client
    body, _ = await confirmed_body(c, replenishment)
    key = uuid4().hex
    assert (await create(c, "replenishment/purchase-orders", body, key)).status_code == 201
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text("DELETE FROM forge.idempotency_keys WHERE organization_id=:org"), identities[0]
        )
        db.execute(
            text(
                "DELETE FROM forge.role_permissions "
                "WHERE organization_id=:org AND permission_code=:p"
            ),
            identities[0] | {"p": permission},
        )
    assert (await c.get("/api/v1/replenishment/suggestions")).status_code == 200
    assert (
        await c.post(
            "/api/v1/replenishment/purchase-preview",
            json={k: v for k, v in body.items() if k != "confirmation_hash"},
        )
    ).status_code == 403
    assert (await create(c, "replenishment/purchase-orders", body, key)).status_code == 403


async def test_creation_permission_gives_only_minimal_warehouse_options(
    catalog_client, replenishment, identities
):
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions "
                "WHERE organization_id=:org AND permission_code='warehouse.read'"
            ),
            identities[0],
        )
    assert (await catalog_client.get("/api/v1/warehouses")).status_code == 403
    result = await catalog_client.get("/api/v1/replenishment/warehouses")
    assert result.status_code == 200, result.text
    assert result.json()["items"] == [
        {"id": replenishment["warehouse"]["id"], "code": "RW", "name": "补货收货仓"}
    ]


async def test_basis_and_unit_changes_require_new_review(catalog_client, replenishment):
    c = catalog_client
    body, _ = await confirmed_body(c, replenishment)
    await opening(c, purchase_body(replenishment), "1")
    response = await create(c, "replenishment/purchase-orders", body)
    assert response.status_code == 409 and response.json()["code"] == "REPLENISHMENT_BASIS_CHANGED"
    box = (await create(c, "units", {"code": "BOX", "name": "箱"})).json()
    conversion = (
        await create(
            c,
            "product-units",
            {
                "product_id": replenishment["product"]["id"],
                "unit_id": box["id"],
                "unit_to_base_factor": "10",
            },
        )
    ).json()
    body, result = await confirmed_body(c, replenishment, unit_id=box["id"])
    assert D(result["lines"][0]["qty"]) == 2
    changed = await c.put(
        "/api/v1/product-units/" + conversion["id"],
        json={
            "product_id": replenishment["product"]["id"],
            "unit_id": box["id"],
            "unit_to_base_factor": "5",
            "expected_version": 1,
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert changed.status_code == 200, changed.text
    response = await create(c, "replenishment/purchase-orders", body)
    assert (
        response.status_code == 409 and response.json()["code"] == "REPLENISHMENT_PREVIEW_CHANGED"
    )


async def test_missing_price_and_inexact_purchase_unit_require_explicit_decisions(
    catalog_client, replenishment
):
    c = catalog_client
    body = await preview_body(c, replenishment, unit_price=None)
    result = await preview(c, body)
    assert result["can_create"] is False and result["total_amount"] is None
    assert (
        result["lines"][0]["unit_price"] is None and "PRICE_REQUIRED" in result["blocking_reasons"]
    )
    assert (
        await create(
            c,
            "replenishment/purchase-orders",
            body | {"confirmation_hash": result["confirmation_hash"]},
        )
    ).status_code == 409
    box = (await create(c, "units", {"code": "THREE", "name": "三件盒"})).json()
    await create(
        c,
        "product-units",
        {
            "product_id": replenishment["product"]["id"],
            "unit_id": box["id"],
            "unit_to_base_factor": "3",
        },
    )
    body = await preview_body(c, replenishment, unit_id=box["id"], unit_price="0")
    result = await preview(c, body)
    assert (
        "QUANTITY_CONVERSION_REQUIRED" in result["blocking_reasons"]
        and result["lines"][0]["qty"] is None
    )
    invalid = body | {"lines": [body["lines"][0] | {"qty": "7"}]}
    assert (await c.post("/api/v1/replenishment/purchase-preview", json=invalid)).status_code == 422
    valid = invalid | {
        "lines": [invalid["lines"][0] | {"quantity_reason": "按供货包装明确采购7盒"}]
    }
    result = await preview(c, valid)
    assert result["can_create"] and D(result["lines"][0]["base_qty"]) == 21
    assert D(result["total_amount"]) == 0 and result["lines"][0]["quantity_source"] == "MANUAL"


@pytest.mark.parametrize("value", [0.1, "NaN", "Infinity", "-1", "0.0000001", "100000000000000"])
async def test_unsafe_prices_rejected(catalog_client, replenishment, value):
    body = await preview_body(catalog_client, replenishment, unit_price=value)
    r = await catalog_client.post("/api/v1/replenishment/purchase-preview", json=body)
    assert r.status_code == 422, r.text


async def test_history_price_uses_frozen_factor_and_snapshot_conflicts(
    catalog_client, replenishment
):
    c = catalog_client
    box = (await create(c, "units", {"code": "OLD-BOX", "name": "历史箱"})).json()
    conversion = (
        await create(
            c,
            "product-units",
            {
                "product_id": replenishment["product"]["id"],
                "unit_id": box["id"],
                "unit_to_base_factor": "10",
            },
        )
    ).json()
    order = await po(c, purchase_body(replenishment, "1", box["id"], "50"))
    await purchase_action(c, order)
    receipt, _ = await receive(c, order, "1")
    posted = await purchase_action(c, receipt, "post", document=True)
    assert posted.status_code == 200, posted.text
    changed = await c.put(
        "/api/v1/product-units/" + conversion["id"],
        json={
            "product_id": replenishment["product"]["id"],
            "unit_id": box["id"],
            "unit_to_base_factor": "20",
            "expected_version": 1,
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert changed.status_code == 200
    body, result = await confirmed_body(
        c,
        replenishment,
        unit_price=None,
        price_source="HISTORY",
        qty="2",
        quantity_reason="复核后采购两件",
    )
    line = result["lines"][0]
    assert D(line["unit_price"]) == 5
    assert line["historical_price"]["document_id"] == receipt["id"]
    assert D(line["historical_price"]["unit_to_base_factor"]) == 10
    assert D(line["historical_price"]["unit_price"]) == 50
    assert (
        await purchase_action(c, posted.json(), "reverse", "历史收货冲销", document=True)
    ).status_code == 200
    changed = await create(c, "replenishment/purchase-orders", body)
    assert changed.status_code == 409


async def test_creation_proof_rls_composite_fk_and_immutable(
    catalog_client, replenishment, identities
):
    c = catalog_client
    body, _ = await confirmed_body(c, replenishment)
    result = await create(c, "replenishment/purchase-orders", body)
    assert result.status_code == 201, result.text
    for sql in (
        "UPDATE forge.replenishment_creations SET algorithm_version='bad'",
        "DELETE FROM forge.replenishment_creations",
    ):
        with pytest.raises(DBAPIError):
            async with sessions.begin() as db:
                await set_tenant(db, identities[0]["org"])
                await db.execute(text(sql))
    async with sessions.begin() as db:
        await set_tenant(db, identities[1]["org"])
        assert (
            await db.execute(text("SELECT count(*) FROM forge.replenishment_creations"))
        ).scalar() == 0
        ctx = RuntimeContext(
            identities[1]["org"],
            identities[1]["user"],
            frozenset(queries.READ_PERMISSIONS),
            "tenant-check",
        )
        assert (await queries.suggestions(db, ctx)).items == []
    with pytest.raises(DBAPIError):
        async with sessions.begin() as db:
            await set_tenant(db, identities[1]["org"])
            await db.execute(
                text("""INSERT INTO forge.replenishment_creations
            (organization_id,actor_id,idempotency_key,request_hash,algorithm_version,
            confirmation_hash,basis_snapshot,selection_snapshot,purchase_order_id,receipt,request_id)
            VALUES(:org,:actor,'cross-tenant-key',repeat('a',64),'test',repeat('a',64),'{}','{}',
            :order,'{}','test')"""),
                {
                    "org": identities[1]["org"],
                    "actor": identities[1]["user"],
                    "order": UUID(result.json()["id"]),
                },
            )


@pytest.mark.parametrize("phase", ["purchase_audit", "replenishment_audit", "durable_insert"])
async def test_atomic_rollback_includes_draft_audit_outbox_and_receipts(
    catalog_client, replenishment, identities, monkeypatch, phase
):
    c = catalog_client
    body, _ = await confirmed_body(c, replenishment)
    if phase in {"purchase_audit", "replenishment_audit"}:
        module = commands.orders if phase == "purchase_audit" else commands
        original = module.record_mutation

        async def fail(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("injected audit commit failure")

        monkeypatch.setattr(module, "record_mutation", fail)
    else:
        from sqlalchemy.ext.asyncio import AsyncSession

        original = AsyncSession.execute

        async def fail(self, statement, *args, **kwargs):
            result = await original(self, statement, *args, **kwargs)
            if "INSERT INTO forge.replenishment_creations" in str(statement):
                raise RuntimeError("injected durable receipt failure")
            return result

        monkeypatch.setattr(AsyncSession, "execute", fail)
    result = await create(c, "replenishment/purchase-orders", body)
    assert result.status_code == 500
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        for table in ("purchase_orders", "purchase_order_lines", "replenishment_creations"):
            assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar() == 0
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.audit_events "
                    "WHERE action IN ('purchase.order.create','replenishment.purchase.created')"
                )
            )
        ).scalar() == 0

        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.outbox_events "
                    "WHERE event_type IN ('purchase.order.create','replenishment.purchase.created')"
                )
            )
        ).scalar() == 0
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.idempotency_keys "
                    "WHERE operation LIKE 'replenishment.%' "
                    "OR operation LIKE 'purchase.order.save:%'"
                )
            )
        ).scalar() == 0


@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
async def test_commit_failure_or_lost_response_recovers_from_permanent_proof(
    catalog_client,
    replenishment,
    identities,
    stage,
    monkeypatch,
):
    c = catalog_client
    body, _ = await confirmed_body(c, replenishment)
    key = uuid4().hex

    def fail(session):
        raise RuntimeError("injected transaction boundary failure")

    @asynccontextmanager
    async def lost_response():
        async with sessions.begin() as db:
            yield db
        # The transaction and its connection completed; only acknowledgement was lost.
        fail(None)

    if stage == "before_commit":
        event.listen(Session, stage, fail)
    try:
        with monkeypatch.context() as patch:
            if stage == "after_commit":
                patch.setattr(auth_dependencies, "sessions", SimpleNamespace(begin=lost_response))
            result = await create(c, "replenishment/purchase-orders", body, key)
    finally:
        if stage == "before_commit":
            event.remove(Session, stage, fail)
    assert result.status_code == 500
    with create_engine(settings().migration_database_url).begin() as db:
        count = db.execute(
            text("SELECT count(*) FROM forge.replenishment_creations WHERE organization_id=:org"),
            identities[0],
        ).scalar_one()
        assert count == (1 if stage == "after_commit" else 0)
        db.execute(
            text("DELETE FROM forge.idempotency_keys WHERE organization_id=:org"), identities[0]
        )
    retried = await create(c, "replenishment/purchase-orders", body, key)
    assert retried.status_code == 201, retried.text
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        receipts = (
            (await db.execute(text("SELECT receipt FROM forge.replenishment_creations")))
            .scalars()
            .all()
        )
        assert receipts == [retried.json()]
        assert (await db.execute(text("SELECT count(*) FROM forge.purchase_orders"))).scalar() == 1
