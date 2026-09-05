import asyncio
from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_catalog import catalog_client as catalog_client
from test_catalog import create, product_fixture

from forge_erp.core.config import settings
from forge_erp.core.db import sessions, set_tenant
from forge_erp.modules.inventory.domain.values import InventoryError
from forge_erp.modules.purchasing.domain.values import line_amount, return_amount


@pytest.fixture
async def purchase(catalog_client):
    c = catalog_client
    product, _, _, unit = await product_fixture(c)
    supplier = (await create(c, "suppliers", {"code": "PS", "name": "采购供应商"})).json()
    warehouse = (await create(c, "warehouses", {"code": "PW", "name": "采购仓"})).json()
    body = {
        "supplier_id": supplier["id"],
        "warehouse_id": warehouse["id"],
        "reason": "采购测试",
        "lines": [
            {"product_id": product["id"], "unit_id": unit["id"], "qty": "100", "unit_price": "10"}
        ],
    }
    return body


async def po(c, body):
    r = await create(c, "purchasing/orders", body)
    assert r.status_code == 201, r.text
    return r.json()


async def action(c, row, name="confirm", reason=None, key=None, document=False):
    return await create(
        c,
        "purchasing/" + ("documents" if document else "orders") + "/" + row["id"] + "/" + name,
        {"expected_version": row["version"]} | ({"reason": reason} if reason else {}),
        key,
    )


async def detail(c, row):
    return (await c.get("/api/v1/purchasing/orders/" + row["id"])).json()


def test_purchase_amount_precision_and_return_tail():
    assert line_amount(D(2), D(1250)) == 2500
    assert line_amount(D("0.3"), D("0.000167")) == D("0.0001")
    assert return_amount(D("0.1"), D("0.000167"), D("0.1"), D("0.0001")) == D("0.0001")
    for qty, price in [
        (0.1, D(1)),
        (D("NaN"), D(1)),
        (D(1), D("Infinity")),
        (D(1), D("-1")),
        (D(0), D(1)),
    ]:
        with pytest.raises(InventoryError):
            line_amount(qty, price)


async def test_order_lifecycle_does_not_change_inventory(catalog_client, purchase, identities):
    c = catalog_client
    row = await po(c, purchase)
    assert (await detail(c, row))["receiving_status"] == "UNRECEIVED"
    key = uuid4().hex
    r = await action(c, row, key=key)
    assert r.status_code == 200, r.text
    assert (await action(c, row, key=key)).json() == r.json()
    assert (await action(c, row)).status_code == 409
    confirmed = r.json()
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        assert (
            await db.execute(text("SELECT count(*) FROM forge.inventory_movements"))
        ).scalar() == 0
        assert (
            await db.execute(text("SELECT count(*) FROM forge.inventory_balances"))
        ).scalar() == 0
    updated = await c.put(
        "/api/v1/purchasing/orders/" + row["id"] + "/draft",
        json=purchase | {"expected_version": confirmed["version"]},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert updated.status_code == 409
    closed = await action(c, confirmed, "close", "不再采购剩余")
    assert closed.status_code == 200, closed.text
    assert (await detail(c, row))["receiving_status"] == "UNRECEIVED"
    assert (await action(c, closed.json(), "confirm")).status_code == 409


async def test_order_snapshot_and_version(catalog_client, purchase):
    c = catalog_client
    row = await po(c, purchase)
    update = await c.put(
        "/api/v1/purchasing/orders/" + row["id"] + "/draft",
        json=purchase | {"reason": "changed", "expected_version": 1},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert update.status_code == 200, update.text
    assert (await action(c, row)).json()["code"] == "DOCUMENT_VERSION_CONFLICT"
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text("UPDATE forge.product_units SET version=version+1 WHERE product_id=:id"),
            {"id": purchase["lines"][0]["product_id"]},
        )
    assert (await action(c, update.json())).json()["code"] == "UNIT_CONVERSION_CHANGED"
    assert (await action(c, update.json(), "cancel", "重新录入")).status_code == 200


async def test_order_rls_and_database_content_guard(catalog_client, purchase, identities):
    c = catalog_client
    row = await po(c, purchase)
    await action(c, row)
    for sql in [
        "UPDATE forge.purchase_orders SET reason='bad',version=version+1 WHERE id=:id",
        "DELETE FROM forge.purchase_order_lines WHERE order_id=:id",
    ]:
        with pytest.raises(DBAPIError):
            async with sessions.begin() as db:
                await set_tenant(db, identities[0]["org"])
                await db.execute(text(sql), {"id": row["id"]})
    async with sessions.begin() as db:
        await set_tenant(db, identities[1]["org"])
        for table in [
            "purchase_orders",
            "purchase_order_lines",
            "purchase_documents",
            "purchase_document_lines",
        ]:
            assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar() == 0
    async with sessions.begin() as db:
        assert (await db.execute(text("SELECT count(*) FROM forge.purchase_orders"))).scalar() == 0


async def receive(c, order, qty="100", price=None):
    source = await detail(c, order)
    body = {
        "source_id": order["id"],
        "reason": "分批收货",
        "lines": [{"source_line_id": source["lines"][0]["id"], "qty": qty, "unit_price": price}],
    }
    r = await create(c, "purchasing/receipts", body)
    assert r.status_code == 201, r.text
    return r.json(), body


async def stock_document(c, row):
    r = await c.get("/api/v1/purchasing/documents/" + row["id"])
    assert r.status_code == 200, r.text
    return r.json()


async def return_draft(c, receipt, qty):
    source = await stock_document(c, receipt)
    body = {
        "source_id": receipt["id"],
        "reason": "采购退货",
        "lines": [{"source_line_id": source["lines"][0]["id"], "qty": qty}],
    }
    r = await create(c, "purchasing/returns", body)
    assert r.status_code == 201, r.text
    return r.json(), body


async def test_partial_receipts_and_current_cost_return(catalog_client, purchase):
    c = catalog_client
    order = await po(c, purchase | {"lines": [purchase["lines"][0] | {"qty": "200"}]})
    await action(c, order)
    first, _ = await receive(c, order, "100", "10")
    assert (await action(c, first, "post", document=True)).status_code == 200
    assert (await detail(c, order))["receiving_status"] == "PARTIAL"
    second, _ = await receive(c, order, "100", "12")
    posted = await action(c, second, "post", document=True)
    assert posted.status_code == 200, posted.text
    assert (await detail(c, order))["receiving_status"] == "RECEIVED"
    ret, _ = await return_draft(c, first, "50")
    r = await action(c, ret, "post", document=True)
    assert r.status_code == 200, r.text
    row = await stock_document(c, ret)
    assert D(row["lines"][0]["amount"]) == 500
    assert D(row["lines"][0]["inventory_value_delta"]) == -550
    assert D(row["lines"][0]["valuation_difference"]) == -50
    assert (await detail(c, order))["receiving_status"] == "RECEIVED"
    assert D((await detail(c, order))["lines"][0]["returned_base_qty"]) == 50
    original = await stock_document(c, first)
    assert D(original["lines"][0]["returnable_qty"]) == 50
    undo = await action(c, r.json(), "reverse", "误退", document=True)
    assert undo.status_code == 200, undo.text
    assert D((await stock_document(c, first))["lines"][0]["returnable_qty"]) == 100
    assert (await action(c, posted.json(), "reverse", "较早业务", document=True)).json()[
        "code"
    ] == "REVERSAL_DEPENDENCY_CONFLICT"
    history = (await c.get("/api/v1/purchasing/price-history")).json()
    assert [D(x["unit_price"]) for x in history["items"]] == [12, 10]


async def test_receipt_reversal_updates_order_and_generic_inventory_rejected(
    catalog_client, purchase
):
    c = catalog_client
    order = await po(c, purchase)
    await action(c, order)
    row, _ = await receive(c, order)
    assert (
        await create(c, "inventory/documents/" + row["id"] + "/post", {"expected_version": 1})
    ).json()["code"] == "PURCHASE_COMMAND_REQUIRED"
    r = await action(c, row, "post", document=True)
    assert r.status_code == 200, r.text
    assert (await c.get("/api/v1/inventory/documents/" + row["id"])).json()[
        "type"
    ] == "PURCHASE_RECEIPT"
    assert (await action(c, r.json(), "reverse", "收错", document=True)).status_code == 200
    assert (await detail(c, order))["receiving_status"] == "UNRECEIVED"
    assert (await c.get("/api/v1/purchasing/price-history")).json()["items"] == []


async def test_concurrent_receipts_cannot_over_receive(catalog_client, purchase):
    c = catalog_client
    order = await po(c, purchase)
    await action(c, order)
    a, _ = await receive(c, order, "70")
    b, _ = await receive(c, order, "70")
    results = await asyncio.gather(
        action(c, a, "post", document=True), action(c, b, "post", document=True)
    )
    assert sorted(x.status_code for x in results) == [200, 409]
    assert next(x for x in results if x.status_code == 409).json()["code"] == "OVER_RECEIPT"
    assert D((await detail(c, order))["lines"][0]["received_base_qty"]) == 70
    successful = next(x.json() for x in results if x.status_code == 200)
    assert (await action(c, successful, "post", document=True)).status_code == 409


async def test_concurrent_returns_and_document_immutability(catalog_client, purchase, identities):
    c = catalog_client
    order = await po(c, purchase)
    await action(c, order)
    received, _ = await receive(c, order)
    await action(c, received, "post", document=True)
    a, _ = await return_draft(c, received, "70")
    b, _ = await return_draft(c, received, "70")
    results = await asyncio.gather(
        action(c, a, "post", document=True), action(c, b, "post", document=True)
    )
    assert sorted(x.status_code for x in results) == [200, 409]
    assert next(x for x in results if x.status_code == 409).json()["code"] == "OVER_RETURN"
    for sql in [
        "DELETE FROM forge.purchase_document_lines WHERE document_id=:id",
        "UPDATE forge.inventory_documents SET reason='changed' WHERE id=:id",
    ]:
        with pytest.raises(DBAPIError):
            async with sessions.begin() as db:
                await set_tenant(db, identities[0]["org"])
                await db.execute(text(sql), {"id": received["id"]})


async def test_closed_order_blocks_receipt_but_allows_return(catalog_client, purchase):
    c = catalog_client
    order = await po(c, purchase)
    confirmed = (await action(c, order)).json()
    first, _ = await receive(c, order, "30")
    await action(c, first, "post", document=True)
    pending, _ = await receive(c, order, "70")
    assert (await action(c, confirmed, "cancel", "取消")).json()["code"] == "ORDER_HAS_RECEIPTS"
    assert (await action(c, confirmed, "close", "供应商停供")).status_code == 200
    assert (await action(c, pending, "post", document=True)).status_code == 409
    ret, _ = await return_draft(c, first, "10")
    assert (await action(c, ret, "post", document=True)).status_code == 200
    assert (await detail(c, order))["status"] == "CLOSED"
    assert (await detail(c, order))["receiving_status"] == "PARTIAL"
