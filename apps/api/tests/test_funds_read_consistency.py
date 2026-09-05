"""Real order locks and historical settlement evidence keep read models truthful."""

import asyncio
from decimal import Decimal as D

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_funds_integration import cash, enable, returned, source
from test_purchasing import action as purchase_action
from test_purchasing import po, receive
from test_purchasing import purchase as purchase
from test_sales_orders import action, opening, so
from test_sales_orders import sale as sale
from test_sales_shipments import post_shipment, shipment

from forge_erp.core.db import sessions, set_tenant
from forge_erp.modules.purchasing.application import orders


async def test_purchase_receipt_waits_for_order_detail_financial_snapshot(
    catalog_client, purchase, identities, monkeypatch
):
    c = catalog_client
    await enable(c)
    order = await po(c, purchase)
    confirmed = await purchase_action(c, order)
    assert confirmed.status_code == 200, confirmed.text
    draft, _ = await receive(c, order, "3")
    quantities_read, continue_read, writer_started = (asyncio.Event() for _ in range(3))
    original_quantities = orders.quantities
    original_execute = AsyncSession.execute
    reader_pid = writer_pid = None

    async def pause_reader(db, ctx, order_id):
        nonlocal reader_pid
        result = await original_quantities(db, ctx, order_id)
        if ctx.request_id == "funds-read-consistency":
            reader_pid = (await db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            quantities_read.set()
            await asyncio.wait_for(continue_read.wait(), 5)
        return result

    async def observe_writer(self, statement, *args, **kwargs):
        nonlocal writer_pid
        sql = " ".join(str(statement).split())
        if "SELECT * FROM forge.purchase_orders" in sql and "FOR UPDATE" in sql:
            writer_pid = (
                await original_execute(self, text("SELECT pg_backend_pid()"))
            ).scalar_one()
            writer_started.set()
        return await original_execute(self, statement, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(orders, "quantities", pause_reader)
        patch.setattr(AsyncSession, "execute", observe_writer)
        reading = asyncio.create_task(
            c.get(
                "/api/v1/purchasing/orders/" + order["id"],
                headers={"X-Request-ID": "funds-read-consistency"},
            )
        )
        posting = None
        try:
            await asyncio.wait_for(quantities_read.wait(), 5)
            posting = asyncio.create_task(purchase_action(c, draft, "post", document=True))
            await asyncio.wait_for(writer_started.wait(), 5)
            # Inspect PostgreSQL's actual blocker, rather than guessing from task timing.
            async with asyncio.timeout(5), sessions.begin() as db:
                await set_tenant(db, identities[0]["org"])
                while True:
                    blockers = (
                        await db.execute(text("SELECT pg_blocking_pids(:pid)"), {"pid": writer_pid})
                    ).scalar_one()
                    if reader_pid in blockers:
                        break
                    assert not posting.done(), "Receipt committed before the detail finished"
                    await asyncio.sleep(0.01)
            continue_read.set()
            old = await asyncio.wait_for(reading, 5)
            posted = await asyncio.wait_for(posting, 5)
        finally:
            continue_read.set()
            await asyncio.gather(reading, *([posting] if posting else []), return_exceptions=True)
    assert old.status_code == 200, old.text
    assert old.json()["receiving_status"] == "UNRECEIVED"
    assert D(old.json()["lines"][0]["received_base_qty"]) == 0
    assert D(old.json()["funds"]["source_amount"]) == 0
    assert old.json()["funds"].get("settlement_status") is None
    assert posted.status_code == 200, posted.text
    current = (await c.get("/api/v1/purchasing/orders/" + order["id"])).json()
    assert current["receiving_status"] == "PARTIAL"
    assert D(current["lines"][0]["received_base_qty"]) == 3
    assert D(current["funds"]["source_amount"]) == 30


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_historical_partial_settlement_stays_separate_from_current_cash(
    catalog_client, sale, side
):
    c = catalog_client
    if side == "AR":
        body = sale | {"lines": [sale["lines"][0] | {"qty": "100", "unit_price": "1"}]}
        await opening(c, body, "100")
        order = await so(c, body)
        assert (await action(c, order)).status_code == 200
        draft, _ = await shipment(c, order, "100")
        posted = await post_shipment(c, draft)
        party_id, prefix, returned_qty = sale["customer_id"], "sales", "50"
    else:
        supplier = await create(c, "suppliers", {"code": "HISTORY-PARTIAL", "name": "历史供应商"})
        assert supplier.status_code == 201, supplier.text
        purchase = {
            "supplier_id": supplier.json()["id"],
            "warehouse_id": sale["warehouse_id"],
            "reason": "历史采购原单",
            "lines": [
                {
                    "product_id": sale["lines"][0]["product_id"],
                    "unit_id": sale["lines"][0]["unit_id"],
                    "qty": "100",
                    "unit_price": "10",
                }
            ],
        }
        order = await po(c, purchase)
        assert (await purchase_action(c, order)).status_code == 200
        draft, _ = await receive(c, order, "10")
        posted = await purchase_action(c, draft, "post", document=True)
        party_id, prefix, returned_qty = purchase["supplier_id"], "purchasing", "5"
    assert posted.status_code == 200, posted.text
    flow = {"doc": posted.json(), "order": order, "party": party_id, "prefix": prefix, "side": side}
    await enable(c)
    origin = await create(
        c,
        "funds/openings",
        {"side": side, "party_id": party_id, "amount": "40", "reason": "历史已结60尚欠40"},
    )
    assert origin.status_code == 201, origin.text
    mapped = await create(
        c,
        "funds/legacy-bindings",
        {
            "side": side,
            "source_document_id": flow["doc"]["id"],
            "opening_source_id": origin.json()["id"],
            "amount": "40",
            "reason": "原单100、历史已结60，核对来源期初",
        },
    )
    assert mapped.status_code == 201, mapped.text
    partial = await source(c, flow)
    assert D(partial["commercial_amount"]) == 100
    assert partial["settlement_status"] == "PARTIAL"
    assert D(partial["historically_settled_amount"]) == 60
    assert D(partial["settled_amount"]) == 0 and D(partial["balance"]) == 40
    order_funds = (await c.get(f"/api/v1/{prefix}/orders/{order['id']}")).json()["funds"]
    assert order_funds["settlement_status"] == "PARTIAL"
    assert D(order_funds["historically_settled_amount"]) == 60
    assert D(order_funds["settled_amount"]) == 0
    await cash(c, flow, "10")
    assert D((await source(c, flow))["settled_amount"]) == 10
    _, return_response = await returned(c, flow, returned_qty)
    assert return_response.status_code == 200, return_response.text
    credit = await source(c, flow)
    assert D(credit["refund_amount"]) == 20
    assert credit["settlement_status"] == "PAID"
    await cash(c, flow, "20", "REFUND")
    final = await source(c, flow)
    assert final["status"] == "SETTLED" and final["settlement_status"] == "PAID"
    assert D(final["balance"]) == 0
    assert D(final["historically_settled_amount"]) == 60
    assert D(final["settled_amount"]) == 10 and D(final["refunded_amount"]) == 20
    summary = (
        await c.get("/api/v1/funds/summary", params={"side": side, "party_id": party_id})
    ).json()
    assert D(summary["settled_amount"]) == 10 and D(summary["refunded_amount"]) == 20
    assert D(summary["balance"]) == 0
