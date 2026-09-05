import asyncio
from decimal import Decimal as D
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_purchasing import action as purchase_action
from test_sales_orders import action, balance, detail, opening, so
from test_sales_orders import sale as sale
from test_sales_shipments import post_shipment, reconcile_sale, restock, shipment, stock_document

from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions, set_tenant
from forge_erp.modules.inventory.application.engine import InventoryEngine


async def issued(c, sale, qty="3", opening_qty="10"):
    await opening(c, sale, opening_qty)
    order = await so(c, sale)
    confirmed = await action(c, order)
    assert confirmed.status_code == 200, confirmed.text
    row, _ = await shipment(c, order, qty)
    posted = await post_shipment(c, row)
    assert posted.status_code == 200, posted.text
    return confirmed.json(), posted.json()


async def return_draft(c, source, qty="1"):
    original = await stock_document(c, source)
    body = {
        "source_id": source["id"],
        "reason": "销售原单退货",
        "lines": [{"source_line_id": original["lines"][0]["id"], "qty": qty}],
    }
    response = await create(c, "sales/returns", body)
    assert response.status_code == 201, response.text
    return response.json(), body


async def reverse(c, row, key=None):
    return await create(
        c,
        "sales/documents/" + row["id"] + "/reverse",
        {"expected_version": row["version"], "reason": "纠正误操作"},
        key,
    )


async def edit_return(c, row, body, key=None):
    return await c.put(
        "/api/v1/sales/documents/" + row["id"] + "/draft",
        headers={"Idempotency-Key": key or uuid4().hex},
        json=body | {"expected_version": row["version"]},
    )


async def test_return_original_cost_and_fulfillment_baseline(catalog_client, sale, identities):
    c = catalog_client
    sale = sale | {"lines": [sale["lines"][0] | {"qty": "100"}]}
    order, original = await issued(c, sale, "60", "200")
    before = await balance(c, sale)
    row, _ = await return_draft(c, original, "10")
    assert await balance(c, sale) == before
    posted = await post_shipment(c, row)
    assert posted.status_code == 200, posted.text
    assert set(posted.json()) == {"id", "status", "version", "request_id"}
    returned = await stock_document(c, row)
    assert returned["kind"] == "RETURN"
    line = returned["lines"][0]
    assert [D(line[k]) for k in ("qty", "base_qty", "amount", "actual_cost")] == [10, 10, 150, 110]
    b = (await balance(c, sale))[0]
    assert [
        D(b[k]) for k in ("on_hand_qty", "reserved_qty", "available_qty", "inventory_value")
    ] == [150, 40, 110, 1650]
    info = await detail(c, order)
    assert info["fulfillment_status"] == "PARTIAL"
    assert [
        D(info["lines"][0][k])
        for k in ("shipped_base_qty", "returned_base_qty", "reserved_base_qty")
    ] == [60, 10, 40]
    original_line = (await stock_document(c, original))["lines"][0]
    assert D(original_line["amount"]) - D(line["amount"]) == 750
    assert D(original_line["actual_cost"]) - D(line["actual_cost"]) == 550
    assert (await action(c, order, "close", "余量不再发货")).status_code == 200
    assert D((await balance(c, sale))[0]["reserved_qty"]) == 0
    await reconcile_sale(sale, identities[0])


async def test_return_uses_frozen_issue_cost_after_current_average_changes(catalog_client, sale):
    c = catalog_client
    _, original = await issued(c, sale)
    receipt = await restock(c, sale, "10", "30")
    assert (await purchase_action(c, receipt, "post", document=True)).status_code == 200
    before = (await balance(c, sale))[0]
    assert D(before["inventory_value"]) == 377
    row, _ = await return_draft(c, original, "2")
    assert (await post_shipment(c, row)).status_code == 200
    line = (await stock_document(c, row))["lines"][0]
    assert D(line["actual_cost"]) == 22
    assert D(line["amount"]) == 30
    after = (await balance(c, sale))[0]
    assert D(after["on_hand_qty"]) == 19
    assert D(after["inventory_value"]) == 399
    assert D((await stock_document(c, original))["lines"][0]["actual_cost"]) == 33


async def test_return_freezes_original_packaging_after_factor_change(catalog_client, sale):
    c = catalog_client
    await opening(c, sale, "2000")
    box = (await create(c, "units", {"code": "RETURN-BOX", "name": "退货箱"})).json()
    conversion = (
        await create(
            c,
            "product-units",
            {
                "product_id": sale["lines"][0]["product_id"],
                "unit_id": box["id"],
                "unit_to_base_factor": "1000",
            },
        )
    ).json()
    order = await so(c, sale | {"lines": [sale["lines"][0] | {"unit_id": box["id"], "qty": "2"}]})
    assert (await action(c, order)).status_code == 200
    original, _ = await shipment(c, order, "2")
    assert (await post_shipment(c, original)).status_code == 200
    changed = await c.put(
        "/api/v1/product-units/" + conversion["id"],
        headers={"Idempotency-Key": uuid4().hex},
        json={
            "product_id": sale["lines"][0]["product_id"],
            "unit_id": box["id"],
            "unit_to_base_factor": "800",
            "expected_version": conversion["version"],
        },
    )
    assert changed.status_code == 200, changed.text
    row, _ = await return_draft(c, original, "1")
    assert (await post_shipment(c, row)).status_code == 200
    line = (await stock_document(c, row))["lines"][0]
    assert [D(line[k]) for k in ("qty", "unit_to_base_factor", "base_qty", "actual_cost")] == [
        1,
        1000,
        1000,
        11000,
    ]
    assert D((await balance(c, sale))[0]["reserved_qty"]) == 0


async def test_full_returns_clear_sales_and_cost_tails_independently(
    catalog_client, sale, identities
):
    c = catalog_client
    sale = sale | {"lines": [sale["lines"][0] | {"qty": "3", "unit_price": "0.666667"}]}
    receipt = await restock(c, sale, "3", "0.333333")
    assert (await purchase_action(c, receipt, "post", document=True)).status_code == 200
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    original, _ = await shipment(c, order, "3")
    assert (await post_shipment(c, original)).status_code == 200
    rows = []
    for qty in ("1", "2"):
        row, _ = await return_draft(c, original, qty)
        assert (await post_shipment(c, row)).status_code == 200
        rows.append((await stock_document(c, row))["lines"][0])
    assert [D(x["amount"]) for x in rows] == [D("0.6667"), D("1.3333")]
    assert [D(x["actual_cost"]) for x in rows] == [D("0.3333"), D("0.6667")]
    assert D((await balance(c, sale))[0]["inventory_value"]) == 1
    info = await detail(c, order)
    assert info["fulfillment_status"] == "FULFILLED"
    assert D(info["lines"][0]["reserved_base_qty"]) == 0
    assert D(info["lines"][0]["executable_base_qty"]) == 0
    await reconcile_sale(sale, identities[0])


@pytest.mark.parametrize("tail", ["amount", "cost"])
async def test_changed_return_tail_requires_explicit_draft_refresh(catalog_client, sale, tail):
    c = catalog_client
    sale = sale | {
        "lines": [
            sale["lines"][0] | {"qty": "4", "unit_price": "0.00005" if tail == "amount" else "1"}
        ]
    }
    receipt = await restock(c, sale, "4", "0.00005" if tail == "cost" else "11")
    assert (await purchase_action(c, receipt, "post", document=True)).status_code == 200
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    original, _ = await shipment(c, order, "4")
    assert (await post_shipment(c, original)).status_code == 200
    pending, body = await return_draft(c, original, "2")
    for _ in range(2):
        row, _ = await return_draft(c, original, "1")
        assert (await post_shipment(c, row)).status_code == 200
    before = await balance(c, sale)
    conflict = await post_shipment(c, pending)
    assert conflict.status_code == 409 and conflict.json()["code"] == "RETURN_QUOTE_CHANGED"
    assert await balance(c, sale) == before
    precise = await create(c, "sales/returns", body | {"lines": [body["lines"][0] | {"qty": "1"}]})
    assert precise.status_code == 409 and precise.json()["code"] == "RETURN_PRECISION_CONFLICT"
    updated = await edit_return(c, pending, body)
    assert updated.status_code == 200, updated.text
    assert (await post_shipment(c, updated.json())).status_code == 200
    line = (await stock_document(c, pending))["lines"][0]
    assert D(line["amount" if tail == "amount" else "actual_cost"]) == 0


@pytest.mark.parametrize("cost,price", [("0", "0"), ("11", "5")])
async def test_zero_cost_zero_price_and_below_cost_returns_are_allowed(
    catalog_client, sale, cost, price
):
    c = catalog_client
    sale = sale | {"lines": [sale["lines"][0] | {"qty": "3", "unit_price": price}]}
    receipt = await restock(c, sale, "3", cost)
    assert (await purchase_action(c, receipt, "post", document=True)).status_code == 200
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    original, _ = await shipment(c, order, "3")
    assert (await post_shipment(c, original)).status_code == 200
    row, _ = await return_draft(c, original, "1")
    assert (await post_shipment(c, row)).status_code == 200
    line = (await stock_document(c, row))["lines"][0]
    assert D(line["amount"]) == D(price)
    assert D(line["actual_cost"]) == D(cost)


async def test_competing_return_posts_serialize_against_one_quota(catalog_client, sale, identities):
    c = catalog_client
    order, original = await issued(c, sale)
    first, _ = await return_draft(c, original, "2")
    second, _ = await return_draft(c, original, "2")
    results = await asyncio.gather(post_shipment(c, first), post_shipment(c, second))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert next(r for r in results if r.status_code == 409).json()["code"] == "OVER_RETURN"
    assert D((await detail(c, order))["lines"][0]["returned_base_qty"]) == 2
    assert D((await balance(c, sale))[0]["reserved_qty"]) == 4
    await reconcile_sale(sale, identities[0])


async def test_return_post_and_original_reversal_cannot_both_succeed(
    catalog_client, sale, identities
):
    c = catalog_client
    order, original = await issued(c, sale)
    row, _ = await return_draft(c, original, "1")
    results = await asyncio.gather(post_shipment(c, row), reverse(c, original))
    assert sorted(r.status_code for r in results) == [200, 409]
    b = (await balance(c, sale))[0]
    info = (await detail(c, order))["lines"][0]
    if results[0].status_code == 200:
        assert [D(b[k]) for k in ("on_hand_qty", "reserved_qty")] == [8, 4]
        assert D(info["returned_base_qty"]) == 1
    else:
        assert [D(b[k]) for k in ("on_hand_qty", "reserved_qty")] == [10, 7]
        assert D(info["shipped_base_qty"]) == D(info["returned_base_qty"]) == 0
    await reconcile_sale(sale, identities[0])


async def test_shipment_reverse_restores_exact_original_reservation_and_cost(
    catalog_client, sale, identities
):
    c = catalog_client
    order, original = await issued(c, sale)
    key = uuid4().hex
    undone = await reverse(c, original, key)
    assert undone.status_code == 200, undone.text
    assert set(undone.json()) == {"id", "status", "version", "request_id"}
    assert (await reverse(c, original, key)).json() == undone.json()
    assert (await reverse(c, original)).status_code == 409
    b = (await balance(c, sale))[0]
    assert [D(b[k]) for k in ("on_hand_qty", "reserved_qty", "inventory_value")] == [10, 7, 110]
    info = await detail(c, order)
    assert info["fulfillment_status"] == "UNFULFILLED"
    assert D(info["lines"][0]["executable_base_qty"]) == 7
    replacement, _ = await shipment(c, order, "7")
    assert (await post_shipment(c, replacement)).status_code == 200
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        assert (
            await db.execute(text("SELECT count(*) FROM forge.inventory_reservations"))
        ).scalar_one() == 1
    await reconcile_sale(sale, identities[0])


async def test_return_reverse_restores_quota_without_reopening_fulfillment(
    catalog_client, sale, identities
):
    c = catalog_client
    order, original = await issued(c, sale)
    row, _ = await return_draft(c, original, "2")
    posted = await post_shipment(c, row)
    assert posted.status_code == 200, posted.text
    results = await asyncio.gather(reverse(c, posted.json()), reverse(c, posted.json()))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert [
        D((await balance(c, sale))[0][k])
        for k in ("on_hand_qty", "reserved_qty", "inventory_value")
    ] == [7, 4, 77]
    info = (await detail(c, order))["lines"][0]
    assert D(info["shipped_base_qty"]) == 3 and D(info["returned_base_qty"]) == 0
    # Reversal facts still make the earlier shipment nonterminal.
    blocked = await reverse(c, original)
    assert blocked.status_code == 409 and blocked.json()["code"] == "REVERSAL_DEPENDENCY_CONFLICT"
    fresh, _ = await return_draft(c, original, "3")
    assert (await post_shipment(c, fresh)).status_code == 200
    await reconcile_sale(sale, identities[0])


async def test_new_reservation_counts_as_reversal_dependency(catalog_client, sale):
    c = catalog_client
    _, original = await issued(c, sale)
    later = await so(c, sale | {"lines": [sale["lines"][0] | {"qty": "1"}]})
    assert (await action(c, later)).status_code == 200
    before = await balance(c, sale)
    blocked = await reverse(c, original)
    assert blocked.status_code == 409 and blocked.json()["code"] == "REVERSAL_DEPENDENCY_CONFLICT"
    assert await balance(c, sale) == before


async def test_closed_order_allows_return_and_its_reverse_but_no_shipment_reverse(
    catalog_client, sale
):
    c = catalog_client
    order, original = await issued(c, sale)
    assert (await action(c, order, "close", "停止发货")).status_code == 200
    assert (await reverse(c, original)).status_code == 409
    row, _ = await return_draft(c, original, "1")
    posted = await post_shipment(c, row)
    assert posted.status_code == 200, posted.text
    assert (await reverse(c, posted.json())).status_code == 200
    info = await detail(c, order)
    assert info["status"] == "CLOSED" and info["fulfillment_status"] == "PARTIAL"
    assert D(info["lines"][0]["executable_base_qty"]) == 0
    assert D((await balance(c, sale))[0]["reserved_qty"]) == 0


@pytest.mark.parametrize("table", ["customers", "products", "units", "warehouses", "product_units"])
async def test_return_post_rechecks_active_masters(catalog_client, sale, identities, table):
    c = catalog_client
    _, original = await issued(c, sale)
    row, _ = await return_draft(c, original, "1")
    before = await balance(c, sale)
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        await db.execute(text(f"UPDATE forge.{table} SET active=false,version=version+1"))
    failed = await post_shipment(c, row)
    assert failed.status_code == 409, failed.text
    assert await balance(c, sale) == before
    assert (await stock_document(c, row))["status"] == "DRAFT"


@pytest.mark.parametrize("kind", ["shipment", "return"])
async def test_legal_reversal_uses_frozen_facts_after_masters_deactivated(
    catalog_client, sale, identities, kind
):
    c = catalog_client
    _, original = await issued(c, sale)
    target = original
    if kind == "return":
        row, _ = await return_draft(c, original, "1")
        posted = await post_shipment(c, row)
        assert posted.status_code == 200, posted.text
        target = posted.json()
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        for table in ("customers", "products", "units", "warehouses", "product_units"):
            await db.execute(text(f"UPDATE forge.{table} SET active=false,version=version+1"))
    response = await reverse(c, target)
    assert response.status_code == 200, response.text
    identity = identities[0]
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        ctx = RuntimeContext(
            identity["org"],
            identity["user"],
            frozenset({"inventory.reconcile", "product.cost.read"}),
            "inactive-reconcile",
        )
        engine = InventoryEngine(db, ctx, "inventory.reconcile")
        await engine.lock(
            [(UUID(sale["warehouse_id"]), UUID(sale["lines"][0]["product_id"]))], historical=True
        )
        assert all(not row["differences"] for row in await engine.reconcile())


async def test_return_draft_versions_and_duplicate_posts_preserve_one_fact(catalog_client, sale):
    c = catalog_client
    _, original = await issued(c, sale)
    row, body = await return_draft(c, original)
    before = await balance(c, sale)
    changed = body | {"lines": [body["lines"][0] | {"qty": "2"}]}
    updated = await edit_return(c, row, changed)
    assert updated.status_code == 200, updated.text
    stale = await edit_return(c, row, body)
    assert stale.status_code == 409 and stale.json()["code"] == "DOCUMENT_VERSION_CONFLICT"
    assert await balance(c, sale) == before
    results = await asyncio.gather(
        post_shipment(c, updated.json()), post_shipment(c, updated.json())
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    assert D((await balance(c, sale))[0]["on_hand_qty"]) == 9
    posted = next(r.json() for r in results if r.status_code == 200)
    assert (await edit_return(c, posted, changed)).status_code == 409


async def test_shipment_reverse_racing_close_never_restores_closed_order_reservation(
    catalog_client, sale, identities
):
    c = catalog_client
    order, original = await issued(c, sale)
    undone, closed = await asyncio.gather(
        reverse(c, original), action(c, order, "close", "关闭竞争中的订单")
    )
    assert closed.status_code == 200, closed.text
    assert undone.status_code in {200, 409}, undone.text
    info = await detail(c, order)
    assert info["status"] == "CLOSED"
    assert D(info["lines"][0]["reserved_base_qty"]) == 0
    assert D(info["lines"][0]["executable_base_qty"]) == 0
    b = (await balance(c, sale))[0]
    assert D(b["reserved_qty"]) == 0
    succeeded = undone.status_code == 200
    assert D(b["on_hand_qty"]) == (10 if succeeded else 7)
    assert D(b["inventory_value"]) == (110 if succeeded else 77)
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        reservation = (
            await db.execute(
                text(
                    "SELECT reserved_qty,consumed_qty,released_qty,remaining_qty "
                    "FROM forge.inventory_reservations"
                )
            )
        ).one()
        assert tuple(reservation) == ((7, 0, 7, 0) if succeeded else (7, 3, 4, 0))
    await reconcile_sale(sale, identities[0])
