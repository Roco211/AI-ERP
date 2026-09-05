"""Real commercial posting, historical cutover and settlement remain one ledger chain."""

from datetime import datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_purchasing import action as purchase_action
from test_purchasing import po, receive
from test_purchasing import return_draft as purchase_return
from test_sales_orders import action, balance, detail, opening, so
from test_sales_orders import sale as sale
from test_sales_returns import return_draft
from test_sales_shipments import post_shipment, shipment

from forge_erp.core.config import settings


def today():
    return datetime.now(ZoneInfo(settings().business_timezone)).date().isoformat()


async def enable(c):
    response = await create(
        c, "funds/activate", {"business_date": today(), "reason": "明确资金切换"}
    )
    assert response.status_code == 201, response.text


async def trade(c, sale, side):
    await opening(c, sale, "10")
    if side == "AR":
        order = await so(c, sale)
        assert (await action(c, order)).status_code == 200
        before = await detail(c, order)
        doc, _ = await shipment(c, order, "3")
        posted = await post_shipment(c, doc)
        prefix = "sales"
        party = sale["customer_id"]
    else:
        supplier = (
            await create(c, "suppliers", {"code": "FI-SUP", "name": "资金接入供应商"})
        ).json()
        order = await po(
            c,
            {
                "supplier_id": supplier["id"],
                "warehouse_id": sale["warehouse_id"],
                "reason": "采购资金接入",
                "lines": [
                    {
                        "product_id": sale["lines"][0]["product_id"],
                        "unit_id": sale["lines"][0]["unit_id"],
                        "qty": "7",
                        "unit_price": "15",
                    }
                ],
            },
        )
        assert (await purchase_action(c, order)).status_code == 200
        before = (await c.get("/api/v1/purchasing/orders/" + order["id"])).json()
        doc, _ = await receive(c, order, "3")
        posted = await purchase_action(c, doc, "post", document=True)
        prefix = "purchasing"
        party = supplier["id"]
    assert posted.status_code == 200, posted.text
    assert before["funds"].get("settlement_status") is None
    return {"order": order, "doc": posted.json(), "prefix": prefix, "party": party, "side": side}


async def returned(c, flow, qty="1"):
    draft, _ = (
        await return_draft(c, flow["doc"], qty)
        if flow["side"] == "AR"
        else await purchase_return(c, flow["doc"], qty)
    )
    response = await create(
        c, flow["prefix"] + "/documents/" + draft["id"] + "/post", {"expected_version": 1}
    )
    return draft, response


async def source(c, flow):
    response = await c.get(
        "/api/v1/funds/sources", params={"side": flow["side"], "party_id": flow["party"]}
    )
    assert response.status_code == 200, response.text
    return next(
        row for row in response.json()["items"] if row["source_document_id"] == flow["doc"]["id"]
    )


async def cash(c, flow, amount, kind="SETTLEMENT"):
    origin = await source(c, flow)
    response = await create(
        c,
        "funds/cash",
        {
            "side": flow["side"],
            "party_id": flow["party"],
            "kind": kind,
            "business_date": today(),
            "method": "BANK_TRANSFER",
            "reason": "实际收付核销",
            "allocations": [{"source_id": origin["id"], "amount": amount}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_posted_commercial_amount_partial_settlement_return_and_refund(
    catalog_client, sale, side
):
    c = catalog_client
    await enable(c)
    flow = await trade(c, sale, side)
    origin = await source(c, flow)
    assert D(origin["amount"]) == 45
    assert origin["business_date"] == today()
    assert origin["settlement_status"] == "UNPAID"
    await cash(c, flow, "20")
    origin = await source(c, flow)
    assert D(origin["balance"]) == 25 and origin["settlement_status"] == "PARTIAL"
    await cash(c, flow, "25")
    draft, response = await returned(c, flow)
    assert response.status_code == 200, response.text
    origin = await source(c, flow)
    assert [D(origin[k]) for k in ("amount", "settled_amount", "refund_amount")] == [30, 45, 15]
    assert origin["status"] == "REFUND" and origin["settlement_status"] == "PAID"
    await cash(c, flow, "5", "REFUND")
    assert D((await source(c, flow))["refund_amount"]) == 10
    await cash(c, flow, "10", "REFUND")
    origin = await source(c, flow)
    assert D(origin["balance"]) == 0 and D(origin["refunded_amount"]) == 15
    info = (await c.get(f"/api/v1/{flow['prefix']}/orders/{flow['order']['id']}")).json()
    assert info["status"] == "CONFIRMED"
    assert info["funds"]["settlement_status"] == "PAID"
    assert info["funds"]["integration_status"] == "ACTIVE"
    line = (await c.get(f"/api/v1/{flow['prefix']}/documents/{draft['id']}")).json()["lines"][0]
    value = line["actual_cost"] if side == "AR" else abs(D(line["inventory_value_delta"]))
    assert D(value) != 15  # Refund follows the frozen commercial amount, never inventory cost.


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_legacy_return_requires_mapping_and_transfer_keeps_total_debt(
    catalog_client, sale, side
):
    c = catalog_client
    flow = await trade(c, sale, side)
    await enable(c)
    before = await balance(c, sale)
    draft, response = await returned(c, flow)
    assert response.status_code == 409
    assert response.json()["code"] == "LEGACY_FUNDS_MAPPING_REQUIRED"
    assert await balance(c, sale) == before
    info = (await c.get(f"/api/v1/{flow['prefix']}/orders/{flow['order']['id']}")).json()
    assert info["funds"]["integration_status"] == "INCOMPLETE"
    assert info["funds"].get("settlement_status") is None
    donor = await create(
        c,
        "funds/openings",
        {"side": side, "party_id": flow["party"], "amount": "20", "reason": "核对旧欠款"},
    )
    assert donor.status_code == 201, donor.text
    mapped = await create(
        c,
        "funds/legacy-bindings",
        {
            "side": side,
            "source_document_id": flow["doc"]["id"],
            "opening_source_id": donor.json()["id"],
            "amount": "20",
            "reason": "分拆来源不增债",
        },
    )
    assert mapped.status_code == 201, mapped.text
    totals = (await c.get("/api/v1/funds/summary", params={"side": side})).json()
    assert D(totals["balance"]) == 20
    retried = await create(
        c, f"{flow['prefix']}/documents/{draft['id']}/post", {"expected_version": 1}
    )
    assert retried.status_code == 200, retried.text
    assert D((await source(c, flow))["balance"]) == 5


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_legacy_zero_settlement_binding_can_reverse_original_into_refund(
    catalog_client, sale, side
):
    c = catalog_client
    flow = await trade(c, sale, side)
    await enable(c)
    mapped = await create(
        c,
        "funds/legacy-bindings",
        {
            "side": side,
            "source_document_id": flow["doc"]["id"],
            "amount": "0",
            "reason": "历史已结清，显式确认",
        },
    )
    assert mapped.status_code == 201, mapped.text
    response = await create(
        c,
        f"{flow['prefix']}/documents/{flow['doc']['id']}/reverse",
        {"expected_version": flow["doc"]["version"], "reason": "纠正历史原单"},
    )
    assert response.status_code == 200, response.text
    origin = await source(c, flow)
    assert D(origin["balance"]) == -45
    assert D(origin["settled_amount"]) == 0  # Historical payment is not a new cash receipt.
    await cash(c, flow, "45", "REFUND")
    assert D((await source(c, flow))["balance"]) == 0


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_reversing_pre_cutover_return_uses_frozen_legacy_return_amount(
    catalog_client, sale, side
):
    c = catalog_client
    flow = await trade(c, sale, side)
    draft, posted = await returned(c, flow)
    assert posted.status_code == 200, posted.text
    await enable(c)
    mapped = await create(
        c,
        "funds/legacy-bindings",
        {
            "side": side,
            "source_document_id": flow["doc"]["id"],
            "amount": "0",
            "reason": "历史净额已结清",
        },
    )
    assert mapped.status_code == 201, mapped.text
    response = await create(
        c,
        f"{flow['prefix']}/documents/{draft['id']}/reverse",
        {"expected_version": posted.json()["version"], "reason": "纠正旧退货"},
    )
    assert response.status_code == 200, response.text
    assert D((await source(c, flow))["balance"]) == 15


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_warehouse_posting_creates_funds_without_exposing_amounts(
    catalog_client, sale, side, identities
):
    c = catalog_client
    await enable(c)
    flow = await trade(c, sale, side)
    denied = "funds.ar.read" if side == "AR" else "funds.ap.read"
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                "AND permission_code=:p"
            ),
            {"org": identities[0]["org"], "p": denied},
        )
    info = await c.get(f"/api/v1/{flow['prefix']}/orders/{flow['order']['id']}")
    assert info.status_code == 200 and "funds" not in info.json()
    assert (await c.get("/api/v1/funds/sources", params={"side": side})).status_code == 403
    _, posted = await returned(c, flow)
    assert posted.status_code == 200, posted.text
    assert set(posted.json()) == {"id", "status", "version", "request_id"}
    with create_engine(settings().migration_database_url).begin() as db:
        total = db.execute(
            text("SELECT sum(amount) FROM forge.funds_entries WHERE organization_id=:org"),
            {"org": identities[0]["org"]},
        ).scalar_one()
    assert total == 30


def test_business_timezone_is_explicit_and_validated():
    from forge_erp.core.config import Settings

    assert Settings(business_timezone="Asia/Shanghai").business_timezone == "Asia/Shanghai"
    with pytest.raises(ValueError, match="timezone"):
        Settings(business_timezone="not-a-timezone")
