from decimal import Decimal as D
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_purchasing import action, detail, po, receive, return_draft, stock_document
from test_purchasing import purchase as purchase

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions, set_tenant
from forge_erp.modules.inventory.application.engine import InventoryEngine


@pytest.mark.parametrize(
    "marker",
    [
        "INSERT INTO forge.inventory_movements",
        "UPDATE forge.inventory_balances",
        "UPDATE forge.inventory_documents",
        "INSERT INTO forge.audit_events",
        "INSERT INTO forge.outbox_events",
        "INSERT INTO forge.idempotency_keys",
    ],
)
async def test_receipt_faults_rollback_and_retry(
    catalog_client, purchase, identities, monkeypatch, marker
):
    c = catalog_client
    order = await po(c, purchase)
    await action(c, order)
    row, _ = await receive(c, order)
    original = AsyncSession.execute
    key = uuid4().hex
    injected = False

    async def broken(self, statement, *args, **kwargs):
        nonlocal injected
        result = await original(self, statement, *args, **kwargs)
        if marker in str(statement) and not injected:
            injected = True
            raise RuntimeError("injected procurement failure")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", broken)
        response = await action(c, row, "post", key=key, document=True)
        assert response.status_code == 500
        assert "injected procurement failure" not in response.text
    assert injected
    assert (await stock_document(c, row))["status"] == "DRAFT"
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        assert (
            await db.execute(text("SELECT count(*) FROM forge.inventory_movements"))
        ).scalar() == 0
        assert (
            await db.execute(text("SELECT count(*) FROM forge.inventory_balances"))
        ).scalar() == 0
    assert (await action(c, row, "post", key=key, document=True)).status_code == 200
    assert D((await detail(c, order))["lines"][0]["received_base_qty"]) == 100


async def test_box_price_preserves_total_and_confirmed_conversion(catalog_client, purchase):
    c = catalog_client
    box = (await create(c, "units", {"code": "BOX-P", "name": "采购箱"})).json()
    pid = purchase["lines"][0]["product_id"]
    pu = (
        await create(
            c,
            "product-units",
            {"product_id": pid, "unit_id": box["id"], "unit_to_base_factor": "1000"},
        )
    ).json()
    order = await po(
        c,
        purchase
        | {
            "lines": [
                {"product_id": pid, "unit_id": box["id"], "qty": "2", "unit_price": "1.234567"}
            ]
        },
    )
    await action(c, order)
    changed = await c.put(
        "/api/v1/product-units/" + pu["id"],
        json={
            "expected_version": pu["version"],
            "product_id": pid,
            "unit_id": box["id"],
            "unit_to_base_factor": "800",
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert changed.status_code == 200
    row, _ = await receive(c, order, "2")
    assert (await action(c, row, "post", document=True)).status_code == 200
    line = (await stock_document(c, row))["lines"][0]
    assert D(line["base_qty"]) == 2000 and D(line["unit_to_base_factor"]) == 1000
    assert D(line["amount"]) == D("2.4691") == D(line["inventory_value_delta"])


async def test_return_preserves_reserved_stock(catalog_client, purchase, identities):
    c = catalog_client
    order = await po(c, purchase)
    await action(c, order)
    row, _ = await receive(c, order)
    await action(c, row, "post", document=True)
    source = await stock_document(c, row)
    line = source["lines"][0]
    ctx = RuntimeContext(
        identities[0]["org"], identities[0]["user"], frozenset({"purchase.receive"}), "reserve-test"
    )
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        e = InventoryEngine(db, ctx, "purchase.receive")
        key = (UUID(source["warehouse_id"]), UUID(line["product_id"]))
        await e.lock([key])
        await e.change(key, UUID(line["id"]), uuid4(), "RESERVE", D(90))
    ret, _ = await return_draft(c, row, "11")
    assert (await action(c, ret, "post", document=True)).json()["code"] == "INSUFFICIENT_STOCK"
    assert D((await stock_document(c, row))["lines"][0]["returnable_qty"]) == 100


@pytest.mark.parametrize(
    "permission",
    [
        "purchase.order.write",
        "purchase.order.confirm",
        "purchase.order.cancel",
        "purchase.order.close",
        "purchase.receive",
        "purchase.return",
        "purchase.reverse",
        "product.cost.read",
    ],
)
async def test_each_permission_is_checked_before_commands(
    catalog_client, purchase, identities, permission
):
    c = catalog_client
    order = await po(c, purchase)
    draft_order = await po(c, purchase)
    confirmed = (await action(c, order)).json()
    row, receipt_body = await receive(c, order, "50")
    posted = (await action(c, row, "post", document=True)).json()
    ret, return_body = await return_draft(c, row, "10")
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                "AND permission_code=:permission"
            ),
            {"org": identities[0]["org"], "permission": permission},
        )
    if permission == "purchase.order.write":
        r = await create(c, "purchasing/orders", purchase)
    elif permission == "purchase.order.confirm":
        r = await action(c, draft_order)
    elif permission == "purchase.order.cancel":
        r = await action(c, draft_order, "cancel", "取消")
    elif permission == "purchase.order.close":
        r = await action(c, confirmed, "close", "关闭")
    elif permission == "purchase.receive":
        r = await create(c, "purchasing/receipts", receipt_body)
    elif permission == "purchase.return":
        r = await action(c, ret, "post", document=True)
    elif permission == "purchase.reverse":
        r = await action(c, posted, "reverse", "冲销", document=True)
    else:
        r = await action(c, ret, "post", document=True)
    assert r.status_code == 403, r.text


async def test_price_redaction_cross_tenant_and_idempotency_revocation(
    catalog_client, purchase, identities
):
    c = catalog_client
    order = await po(c, purchase)
    await action(c, order)
    row, _ = await receive(c, order)
    key = uuid4().hex
    posted = await action(c, row, "post", key=key, document=True)
    assert posted.status_code == 200
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                "AND permission_code='product.cost.read'"
            ),
            {"org": identities[0]["org"]},
        )
    for path in ["orders", "orders/" + order["id"], "documents", "documents/" + row["id"]]:
        r = await c.get("/api/v1/purchasing/" + path)
        assert r.status_code == 200
        for forbidden in ["unit_price", "amount", "inventory_value_delta", "valuation_difference"]:
            assert forbidden not in r.text
    assert (await c.get("/api/v1/purchasing/price-history")).status_code == 403
    assert (await action(c, row, "post", key=key, document=True)).status_code == 403
    bad = await create(
        c, "purchasing/orders", purchase | {"organization_id": str(identities[1]["org"])}
    )
    assert bad.status_code == 422
    await c.post("/api/v1/auth/logout")
    login = await c.post(
        "/api/v1/auth/login",
        json={
            "organization_code": identities[1]["code"],
            "email": "same@example.test",
            "password": "test-only-password-8472",
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert login.status_code == 200
    for path in ["orders/" + order["id"], "documents/" + row["id"]]:
        assert (await c.get("/api/v1/purchasing/" + path)).status_code == 404
    assert (await action(c, row, "post", document=True)).status_code == 404


async def test_return_rounding_tail_and_source_price_fixed(catalog_client, purchase):
    c = catalog_client
    body = purchase | {"lines": [purchase["lines"][0] | {"qty": "0.3", "unit_price": "0.000167"}]}
    order = await po(c, body)
    await action(c, order)
    row, _ = await receive(c, order, "0.3")
    await action(c, row, "post", document=True)
    a, _ = await return_draft(c, row, "0.1")
    assert (await action(c, a, "post", document=True)).status_code == 200
    b, body = await return_draft(c, row, "0.2")
    assert D((await stock_document(c, b))["amount"]) == D("0.0001")
    assert (await action(c, b, "post", document=True)).status_code == 200
    assert D((await stock_document(c, row))["lines"][0]["returnable_qty"]) == 0
    bad = await create(
        c, "purchasing/returns", body | {"lines": [body["lines"][0] | {"unit_price": "1"}]}
    )
    assert bad.status_code == 409


@pytest.mark.parametrize("value", [0.1, "NaN", "Infinity", "1.0000001", "100000000000000"])
async def test_purchase_rejects_inexact_or_out_of_range_inputs(catalog_client, purchase, value):
    for field in ("qty", "unit_price"):
        body = purchase | {"lines": [purchase["lines"][0] | {field: value}]}
        assert (await create(catalog_client, "purchasing/orders", body)).status_code == 422


async def test_zero_price_and_duplicate_post(catalog_client, purchase):
    import asyncio

    c = catalog_client
    order = await po(c, purchase | {"lines": [purchase["lines"][0] | {"unit_price": "0"}]})
    await action(c, order)
    row, _ = await receive(c, order)
    results = await asyncio.gather(
        action(c, row, "post", document=True), action(c, row, "post", document=True)
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    line = (await stock_document(c, row))["lines"][0]
    assert D(line["amount"]) == D(line["inventory_value_delta"]) == 0


async def test_deactivation_serializes_before_receipt(catalog_client, purchase, identities):
    import asyncio

    c = catalog_client
    order = await po(c, purchase)
    await action(c, order)
    row, _ = await receive(c, order)
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        await db.execute(
            text("UPDATE forge.suppliers SET active=false,version=version+1 WHERE id=:id"),
            {"id": purchase["supplier_id"]},
        )
        pending = asyncio.create_task(action(c, row, "post", document=True))
        await asyncio.sleep(0.1)
        assert not pending.done()
    response = await asyncio.wait_for(pending, 5)
    assert response.status_code == 409
    assert (await stock_document(c, row))["status"] == "DRAFT"
    assert D((await detail(c, order))["lines"][0]["received_base_qty"]) == 0


async def test_multiline_receipt_second_line_failure_is_atomic(
    catalog_client, purchase, monkeypatch
):
    c = catalog_client
    original_product = (
        await c.get("/api/v1/products/" + purchase["lines"][0]["product_id"])
    ).json()
    second = (
        await create(
            c,
            "products",
            {
                "sku": "SECOND-PUR",
                "attributes": original_product["attributes"],
                "name": "第二行",
                "category_id": original_product["category_id"],
                "base_unit_id": purchase["lines"][0]["unit_id"],
            },
        )
    ).json()
    body = purchase | {
        "lines": purchase["lines"] + [purchase["lines"][0] | {"product_id": second["id"]}]
    }
    order = await po(c, body)
    await action(c, order)
    source = await detail(c, order)
    row = (
        await create(
            c,
            "purchasing/receipts",
            {
                "source_id": order["id"],
                "reason": "两行原子性",
                "lines": [{"source_line_id": x["id"], "qty": "100"} for x in source["lines"]],
            },
        )
    ).json()
    original = InventoryEngine.change
    calls = 0

    async def broken(self, *args, **kwargs):
        nonlocal calls
        result = await original(self, *args, **kwargs)
        calls += 1
        if calls == 2:
            raise RuntimeError("second line failure")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(InventoryEngine, "change", broken)
        assert (await action(c, row, "post", document=True)).status_code == 500
    assert all(D(x["received_base_qty"]) == 0 for x in (await detail(c, order))["lines"])
    assert (await action(c, row, "post", document=True)).status_code == 200


async def test_purchase_audit_outbox_and_redis_independence(
    catalog_client, purchase, identities, monkeypatch
):
    from redis.asyncio import Redis

    from forge_erp.workers.outbox import drain_outbox

    c = catalog_client
    order = await po(c, purchase)
    await action(c, order)
    row, _ = await receive(c, order)

    async def unavailable(*args, **kwargs):
        raise ConnectionError("Redis unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(Redis, "execute_command", unavailable)
        assert (await action(c, row, "post", document=True)).status_code == 200
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        audit = (
            await db.execute(
                text(
                    "SELECT before,after FROM forge.audit_events "
                    "WHERE action='purchase.document.post' AND resource_id=:id"
                ),
                {"id": row["id"]},
            )
        ).one()
        assert audit.before["document"]["status"] == "DRAFT"
        assert audit.after["document"]["status"] == "POSTED"
        assert D(audit.after["lines"][0]["inventory_value_delta"]) == 1000
        event = (
            await db.execute(
                text(
                    "SELECT payload,processed_at FROM forge.outbox_events "
                    "WHERE event_type='purchase.document.post'"
                )
            )
        ).one()
        assert set(event.payload) == {"resource_id", "version"}
        assert event.processed_at is None
    await drain_outbox(identities[0]["org"])
    await drain_outbox(identities[0]["org"])
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        assert (
            await db.execute(
                text(
                    "SELECT attempts FROM forge.outbox_events "
                    "WHERE event_type='purchase.document.post'"
                )
            )
        ).scalar_one() == 1
        assert (
            await db.execute(text("SELECT count(*) FROM forge.inventory_movements"))
        ).scalar_one() == 1


async def test_source_ownership_and_bounded_queries(catalog_client, purchase):
    c = catalog_client
    a = await po(c, purchase)
    b = await po(c, purchase)
    await action(c, a)
    await action(c, b)
    wrong_line = (await detail(c, b))["lines"][0]["id"]
    bad = await create(
        c,
        "purchasing/receipts",
        {
            "source_id": a["id"],
            "reason": "跨来源",
            "lines": [{"source_line_id": wrong_line, "qty": "1"}],
        },
    )
    assert bad.status_code == 404
    assert (
        await create(c, "purchasing/orders", purchase | {"lines": purchase["lines"] * 201})
    ).status_code == 422
    assert (await c.get("/api/v1/purchasing/orders?page_size=101")).status_code == 422
    assert (await c.get("/api/v1/purchasing/orders?page_size=1")).json()["total"] == 2
