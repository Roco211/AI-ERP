"""Zero commercial amounts still carry real stock and financial provenance."""

from decimal import Decimal as D

import pytest
from sqlalchemy import text
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_funds_integration import enable, today
from test_purchasing import action as purchase_action
from test_purchasing import po, receive
from test_sales_orders import action, balance, opening, so
from test_sales_orders import sale as sale
from test_sales_shipments import post_shipment, shipment

from forge_erp.core.db import sessions, set_tenant


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_zero_price_posting_keeps_source_stock_cost_and_rejects_zero_cash(
    catalog_client, sale, identities, side
):
    c = catalog_client
    await enable(c)
    # Existing stock has a real cost, independent of this zero-price trade.
    await opening(c, sale, "10")
    if side == "AR":
        body = sale | {"lines": [sale["lines"][0] | {"unit_price": "0"}]}
        order = await so(c, body)
        confirmed = await action(c, order)
        assert confirmed.status_code == 200, confirmed.text
        draft, _ = await shipment(c, order, "3")
        posted = await post_shipment(c, draft)
        party_id, prefix, source_kind = sale["customer_id"], "sales", "SHIPMENT"
        movement_kind, movement_qty, movement_value = "ISSUE", D("-3"), D("-33")
    else:
        supplier = await create(c, "suppliers", {"code": "FREE-RECEIPT", "name": "零价供货商"})
        assert supplier.status_code == 201, supplier.text
        party_id, prefix, source_kind = supplier.json()["id"], "purchasing", "RECEIPT"
        order = await po(
            c,
            {
                "supplier_id": party_id,
                "warehouse_id": sale["warehouse_id"],
                "reason": "明确零价采购",
                "lines": [
                    {
                        "product_id": sale["lines"][0]["product_id"],
                        "unit_id": sale["lines"][0]["unit_id"],
                        "qty": "7",
                        "unit_price": "0",
                    }
                ],
            },
        )
        confirmed = await purchase_action(c, order)
        assert confirmed.status_code == 200, confirmed.text
        draft, _ = await receive(c, order, "3", "0")
        posted = await purchase_action(c, draft, "post", document=True)
        movement_kind, movement_qty, movement_value = "RECEIVE", D("3"), D("0")
    assert posted.status_code == 200, posted.text
    document_id = posted.json()["id"]
    page = await c.get("/api/v1/funds/sources", params={"side": side, "party_id": party_id})
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1
    origin = page.json()["items"][0]
    assert origin["source_document_id"] == document_id and origin["kind"] == source_kind
    assert origin["status"] == "SETTLED" and origin["settlement_status"] == "PAID"
    for field in (
        "commercial_amount",
        "amount",
        "balance",
        "settled_amount",
        "refunded_amount",
        "settlement_amount",
        "refund_amount",
        "historically_settled_amount",
    ):
        assert D(origin[field]) == 0
    detail = await c.get("/api/v1/funds/sources/" + origin["id"])
    assert detail.status_code == 200, detail.text
    assert detail.json()["cash"] == []
    entries = detail.json()["entries"]
    assert len(entries) == 1 and entries[0]["kind"] == "ORIGIN"
    assert entries[0]["document_id"] == document_id and D(entries[0]["amount"]) == 0

    order_detail = await c.get(f"/api/v1/{prefix}/orders/{order['id']}")
    assert order_detail.status_code == 200, order_detail.text
    order_read = order_detail.json()
    assert order_read["status"] == "CONFIRMED"
    assert order_read["funds"]["integration_status"] == "ACTIVE"
    assert order_read["funds"]["settlement_status"] == "PAID"
    assert D(order_read["funds"]["source_amount"]) == 0
    stock = (await balance(c, sale))[0]
    if side == "AR":
        assert order_read["fulfillment_status"] == "PARTIAL"
        assert D(order_read["net_sales_amount"]) == 0
        assert D(order_read["net_cost"]) == 33 and D(order_read["gross_margin"]) == -33
        assert D(stock["on_hand_qty"]) == 7 and D(stock["reserved_qty"]) == 4
        assert D(stock["inventory_value"]) == 77 and D(stock["avg_unit_cost"]) == 11
    else:
        assert order_read["receiving_status"] == "PARTIAL"
        assert D(stock["on_hand_qty"]) == 13 and D(stock["reserved_qty"]) == 0
        assert D(stock["inventory_value"]) == 110
        assert D(stock["avg_unit_cost"]) == D("8.461538")
    for kind in ("SETTLEMENT", "REFUND"):
        refused = await create(
            c,
            "funds/cash",
            {
                "side": side,
                "party_id": party_id,
                "kind": kind,
                "business_date": today(),
                "method": "CASH",
                "reason": "不能登记零额现金",
                "allocations": [{"source_id": origin["id"], "amount": "0"}],
            },
        )
        assert refused.status_code == 422 and refused.json()["code"] == "INVALID_AMOUNT"

    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        movement = (
            (
                await db.execute(
                    text(
                        "SELECT kind,base_qty,value_delta "
                        "FROM forge.inventory_movements "
                        "WHERE organization_id=:org AND document_id=:document "
                        "AND operation_id=:document"
                    ),
                    {"org": identities[0]["org"], "document": document_id},
                )
            )
            .mappings()
            .one()
        )
        assert (movement["kind"], movement["base_qty"], movement["value_delta"]) == (
            movement_kind,
            movement_qty,
            movement_value,
        )
        commercial = (
            await db.execute(
                text(
                    "SELECT commercial_amount FROM forge.funds_sources "
                    "WHERE organization_id=:org AND source_document_id=:document"
                ),
                {"org": identities[0]["org"], "document": document_id},
            )
        ).scalar_one()
        assert commercial == D("0.0000") and commercial.as_tuple().exponent == -4
        assert (
            await db.execute(text("SELECT count(*) FROM forge.funds_cash_documents"))
        ).scalar_one() == 0
        assert (
            await db.execute(text("SELECT count(*) FROM forge.funds_cash_allocations"))
        ).scalar_one() == 0
