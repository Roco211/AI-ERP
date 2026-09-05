import asyncio
from decimal import Decimal as D
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_purchasing import action as purchase_action
from test_purchasing import po, receive
from test_sales_orders import action, balance, detail, opening, so
from test_sales_orders import sale as sale

from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions, set_tenant
from forge_erp.modules.inventory.application.engine import InventoryEngine


async def shipment(c, order, qty="3"):
    source = await detail(c, order)
    body = {
        "source_id": order["id"],
        "reason": "销售分批出库",
        "lines": [{"source_line_id": source["lines"][0]["id"], "qty": qty}],
    }
    response = await create(c, "sales/shipments", body)
    assert response.status_code == 201, response.text
    return response.json(), body


async def post_shipment(c, row, key=None):
    return await create(
        c,
        "sales/documents/" + row["id"] + "/post",
        {"expected_version": row["version"]},
        key,
    )


async def stock_document(c, row):
    response = await c.get("/api/v1/sales/documents/" + row["id"])
    assert response.status_code == 200, response.text
    return response.json()


async def restock(c, sale, qty="10", price="15"):
    supplier = (
        await create(c, "suppliers", {"code": "SS-" + uuid4().hex, "name": "补货供应商"})
    ).json()
    order = await po(
        c,
        {
            "supplier_id": supplier["id"],
            "warehouse_id": sale["warehouse_id"],
            "reason": "出库前补货改变实时均价",
            "lines": [
                {
                    "product_id": sale["lines"][0]["product_id"],
                    "unit_id": sale["lines"][0]["unit_id"],
                    "qty": qty,
                    "unit_price": price,
                }
            ],
        },
    )
    confirmed = await purchase_action(c, order)
    assert confirmed.status_code == 200, confirmed.text
    receipt, _ = await receive(c, order, qty)
    return receipt


async def reconcile_sale(sale, identity):
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        ctx = RuntimeContext(
            identity["org"],
            identity["user"],
            frozenset({"inventory.reconcile", "product.cost.read"}),
            "shipment-reconcile",
        )
        engine = InventoryEngine(db, ctx, "inventory.reconcile")
        await engine.lock(
            [(UUID(sale["warehouse_id"]), UUID(line["product_id"])) for line in sale["lines"]]
        )
        assert all(not row["differences"] for row in await engine.reconcile())


async def test_partial_shipments_consume_only_this_order_reservation(
    catalog_client, sale, identities
):
    c = catalog_client
    sale = sale | {"lines": [sale["lines"][0] | {"qty": "100"}]}
    await opening(c, sale, "200")
    order = await so(c, sale)
    confirmed = await action(c, order)
    assert confirmed.status_code == 200, confirmed.text
    before = await balance(c, sale)
    first, _ = await shipment(c, order, "60")
    assert await balance(c, sale) == before
    key = uuid4().hex
    posted = await post_shipment(c, first, key)
    assert posted.status_code == 200, posted.text
    assert set(posted.json()) == {"id", "status", "version", "request_id"}
    assert (await post_shipment(c, first, key)).json() == posted.json()
    assert (await post_shipment(c, first)).status_code == 409
    b = (await balance(c, sale))[0]
    assert [D(b[k]) for k in ("on_hand_qty", "reserved_qty", "available_qty")] == [140, 40, 100]
    line = (await stock_document(c, first))["lines"][0]
    assert [D(line[k]) for k in ("qty", "base_qty", "unit_price", "amount", "actual_cost")] == [
        60,
        60,
        15,
        900,
        660,
    ]
    info = await detail(c, order)
    assert info["fulfillment_status"] == "PARTIAL"
    assert D(info["lines"][0]["shipped_base_qty"]) == 60
    assert D(info["lines"][0]["reserved_base_qty"]) == 40
    second, _ = await shipment(c, order, "40")
    assert (await post_shipment(c, second)).status_code == 200
    info = await detail(c, order)
    assert info["fulfillment_status"] == "FULFILLED" and info["status"] == "CONFIRMED"
    assert D(info["lines"][0]["executable_base_qty"]) == 0
    assert D((await balance(c, sale))[0]["on_hand_qty"]) == 100
    await reconcile_sale(sale, identities[0])
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT m.line_id,r.line_id AS reservation_line_id,"
                        "il.reservation_source_line_id,m.value_delta "
                        "FROM forge.inventory_movements m "
                        "JOIN forge.inventory_reservations r ON (r.organization_id,r.id)="
                        "(m.organization_id,m.reservation_id) "
                        "JOIN forge.inventory_document_lines il "
                        "ON (il.organization_id,il.id)=(m.organization_id,m.line_id) "
                        "WHERE m.kind='ISSUE' ORDER BY m.sequence"
                    )
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 2
        assert all(r["line_id"] != r["reservation_line_id"] for r in rows)
        assert all(r["reservation_source_line_id"] == r["reservation_line_id"] for r in rows)
        assert [r["value_delta"] for r in rows] == [-660, -440]


async def test_shipment_uses_cost_at_issue_and_preserves_historical_cost(catalog_client, sale):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    receipt = await restock(c, sale)
    assert (await purchase_action(c, receipt, "post", document=True)).status_code == 200
    row, _ = await shipment(c, order)
    assert (await post_shipment(c, row)).status_code == 200
    assert D((await stock_document(c, row))["lines"][0]["actual_cost"]) == 39
    another = await restock(c, sale, "10", "30")
    assert (await purchase_action(c, another, "post", document=True)).status_code == 200
    assert D((await stock_document(c, row))["lines"][0]["actual_cost"]) == 39
    assert D((await stock_document(c, row))["lines"][0]["amount"]) == 45


async def test_shipment_freezes_order_unit_factor_and_price(catalog_client, sale):
    c = catalog_client
    await opening(c, sale, "2000")
    box = (await create(c, "units", {"code": "SALE-BOX", "name": "销售箱"})).json()
    product = sale["lines"][0]["product_id"]
    conversion = (
        await create(
            c,
            "product-units",
            {"product_id": product, "unit_id": box["id"], "unit_to_base_factor": "1000"},
        )
    ).json()
    order = await so(
        c,
        sale
        | {
            "lines": [
                sale["lines"][0] | {"unit_id": box["id"], "qty": "2", "unit_price": "1.234567"}
            ]
        },
    )
    assert (await action(c, order)).status_code == 200
    response = await c.put(
        "/api/v1/product-units/" + conversion["id"],
        headers={"Idempotency-Key": uuid4().hex},
        json={
            "product_id": product,
            "unit_id": box["id"],
            "unit_to_base_factor": "800",
            "expected_version": conversion["version"],
        },
    )
    assert response.status_code == 200, response.text
    row, _ = await shipment(c, order, "2")
    assert (await post_shipment(c, row)).status_code == 200
    line = (await stock_document(c, row))["lines"][0]
    assert [D(line[k]) for k in ("qty", "unit_to_base_factor", "base_qty")] == [2, 1000, 2000]
    assert D(line["unit_price"]) == D("1.234567")
    assert D(line["amount"]) == D("2.4691")
    assert D(line["actual_cost"]) == 22000


async def test_full_issue_consumes_inventory_cost_tail(catalog_client, sale):
    c = catalog_client
    sale = sale | {"lines": [sale["lines"][0] | {"qty": "3"}]}
    receipt = await restock(c, sale, "3", "0.333333")
    assert (await purchase_action(c, receipt, "post", document=True)).status_code == 200
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    first, _ = await shipment(c, order, "1")
    assert (await post_shipment(c, first)).status_code == 200
    second, _ = await shipment(c, order, "2")
    assert (await post_shipment(c, second)).status_code == 200
    assert D((await stock_document(c, first))["lines"][0]["actual_cost"]) == D("0.3333")
    assert D((await stock_document(c, second))["lines"][0]["actual_cost"]) == D("0.6667")
    b = (await balance(c, sale))[0]
    assert all(D(b[k]) == 0 for k in ("on_hand_qty", "reserved_qty", "inventory_value"))


async def test_shipment_draft_edit_stale_version_and_posted_immutability(catalog_client, sale):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    row, body = await shipment(c, order)
    before = await balance(c, sale)
    body = body | {"lines": [body["lines"][0] | {"qty": "4"}], "expected_version": row["version"]}
    updated = await c.put(
        "/api/v1/sales/documents/" + row["id"] + "/draft",
        headers={"Idempotency-Key": uuid4().hex},
        json=body,
    )
    assert updated.status_code == 200, updated.text
    assert await balance(c, sale) == before
    assert (await post_shipment(c, row)).json()["code"] == "DOCUMENT_VERSION_CONFLICT"
    posted = await post_shipment(c, updated.json())
    assert posted.status_code == 200, posted.text
    changed = await c.put(
        "/api/v1/sales/documents/" + row["id"] + "/draft",
        headers={"Idempotency-Key": uuid4().hex},
        json=body | {"expected_version": posted.json()["version"]},
    )
    assert changed.status_code == 409
    assert D((await stock_document(c, row))["lines"][0]["qty"]) == 4


async def test_concurrent_shipments_cannot_over_ship_or_borrow_other_reservations(
    catalog_client, sale, identities
):
    c = catalog_client
    await opening(c, sale, "20")
    order = await so(c, sale)
    other = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    assert (await action(c, other)).status_code == 200
    first, _ = await shipment(c, order, "5")
    second, _ = await shipment(c, order, "5")
    results = await asyncio.wait_for(
        asyncio.gather(post_shipment(c, first), post_shipment(c, second)), 10
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    assert next(r for r in results if r.status_code == 409).json()["code"] == "OVER_SHIPMENT"
    info = await detail(c, order)
    assert D(info["lines"][0]["shipped_base_qty"]) == 5
    assert D(info["lines"][0]["reserved_base_qty"]) == 2
    assert D((await detail(c, other))["lines"][0]["reserved_base_qty"]) == 7
    assert D((await balance(c, sale))[0]["reserved_qty"]) == 9
    await reconcile_sale(sale, identities[0])


async def test_close_and_shipment_race_releases_only_unshipped_reservation(
    catalog_client, sale, identities
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    confirmed = (await action(c, order)).json()
    row, _ = await shipment(c, order)
    posted, closed = await asyncio.wait_for(
        asyncio.gather(post_shipment(c, row), action(c, confirmed, "close", "关闭未发")), 10
    )
    assert closed.status_code == 200, closed.text
    assert posted.status_code in {200, 409}, posted.text
    info = await detail(c, order)
    shipped = D(info["lines"][0]["shipped_base_qty"])
    assert shipped == (3 if posted.status_code == 200 else 0)
    assert info["status"] == "CLOSED"
    assert D(info["lines"][0]["executable_base_qty"]) == 0
    assert D(info["lines"][0]["reserved_base_qty"]) == 0
    assert D((await balance(c, sale))[0]["on_hand_qty"]) == 10 - shipped
    assert (await post_shipment(c, row)).status_code == 409
    await reconcile_sale(sale, identities[0])


async def test_shipment_and_purchase_same_key_have_serializable_cost(
    catalog_client, sale, identities
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    row, _ = await shipment(c, order)
    receipt = await restock(c, sale)
    results = await asyncio.wait_for(
        asyncio.gather(post_shipment(c, row), purchase_action(c, receipt, "post", document=True)),
        10,
    )
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    cost = D((await stock_document(c, row))["lines"][0]["actual_cost"])
    assert cost in {D(33), D(39)}
    b = (await balance(c, sale))[0]
    assert D(b["on_hand_qty"]) == 17 and D(b["inventory_value"]) == 260 - cost
    assert D(b["reserved_qty"]) == 4
    await reconcile_sale(sale, identities[0])


@pytest.mark.parametrize("table", ["customers", "products", "units", "warehouses", "product_units"])
async def test_shipment_rechecks_master_deactivation(catalog_client, sale, identities, table):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    row, _ = await shipment(c, order)
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        await db.execute(text(f"UPDATE forge.{table} SET active=false,version=version+1"))
        pending = asyncio.create_task(post_shipment(c, row))
        await asyncio.sleep(0.1)
        assert not pending.done()
    response = await asyncio.wait_for(pending, 5)
    assert response.status_code == 409, response.text
    assert (await stock_document(c, row))["status"] == "DRAFT"
    assert D((await detail(c, order))["lines"][0]["reserved_base_qty"]) == 7


async def test_partial_order_cannot_cancel_and_closed_draft_cannot_post(catalog_client, sale):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    confirmed = (await action(c, order)).json()
    row, _ = await shipment(c, order)
    assert (await post_shipment(c, row)).status_code == 200
    pending, _ = await shipment(c, order, "4")
    cancelled = await action(c, confirmed, "cancel", "取消")
    assert cancelled.status_code == 409 and cancelled.json()["code"] == "ORDER_HAS_SHIPMENTS"
    assert (await action(c, confirmed, "close", "不再出库")).status_code == 200
    assert (await post_shipment(c, pending)).status_code == 409
    info = await detail(c, order)
    assert info["fulfillment_status"] == "PARTIAL"
    assert D(info["lines"][0]["remaining_base_qty"]) == 4
    assert D(info["lines"][0]["reserved_base_qty"]) == 0


async def test_cancelled_order_invalidates_pending_shipment(catalog_client, sale):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    confirmed = (await action(c, order)).json()
    row, _ = await shipment(c, order)
    assert (await action(c, confirmed, "cancel", "无需出库")).status_code == 200
    assert (await post_shipment(c, row)).status_code == 409
    assert D((await balance(c, sale))[0]["on_hand_qty"]) == 10
    assert D((await balance(c, sale))[0]["reserved_qty"]) == 0


async def test_same_shipment_post_race_only_consumes_reservation_once(catalog_client, sale):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    row, _ = await shipment(c, order)
    results = await asyncio.wait_for(
        asyncio.gather(post_shipment(c, row), post_shipment(c, row)), 10
    )
    assert sorted(response.status_code for response in results) == [200, 409]
    assert D((await detail(c, order))["lines"][0]["shipped_base_qty"]) == 3
    b = (await balance(c, sale))[0]
    assert D(b["on_hand_qty"]) == 7 and D(b["reserved_qty"]) == 4


async def test_manual_issue_cannot_consume_sales_reserved_quantity(
    catalog_client, sale, identities
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    row, _ = await shipment(c, order)
    adjustment = await create(
        c,
        "inventory/adjustments",
        {
            "warehouse_id": sale["warehouse_id"],
            "reason": "不得取用销售订单占用",
            "lines": [
                {
                    "product_id": sale["lines"][0]["product_id"],
                    "unit_id": sale["lines"][0]["unit_id"],
                    "qty": "4",
                    "direction": "OUT",
                    "input_unit_cost": None,
                }
            ],
        },
    )
    assert adjustment.status_code == 201, adjustment.text
    adjustment_row = adjustment.json()
    posted, refused = await asyncio.wait_for(
        asyncio.gather(
            post_shipment(c, row),
            create(
                c,
                "inventory/documents/" + adjustment_row["id"] + "/post",
                {"expected_version": adjustment_row["version"]},
            ),
        ),
        10,
    )
    assert posted.status_code == 200, posted.text
    assert refused.status_code == 409 and refused.json()["code"] == "INSUFFICIENT_STOCK"
    b = (await balance(c, sale))[0]
    assert [D(b[k]) for k in ("on_hand_qty", "reserved_qty", "available_qty")] == [7, 4, 3]
    await reconcile_sale(sale, identities[0])
