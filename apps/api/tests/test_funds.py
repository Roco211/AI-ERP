from decimal import Decimal as D
from uuid import uuid4

import pytest
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_funds_integration import returned, trade
from test_sales_orders import sale as sale


async def activate(c, key=None):
    r = await create(
        c, "funds/activate", {"business_date": "2026-01-01", "reason": "明确切点"}, key
    )
    assert r.status_code == 201, r.text
    return r


async def party(c, side):
    r = await create(
        c,
        "customers" if side == "AR" else "suppliers",
        {"code": "FP_" + uuid4().hex, "name": "往来_%\\资料"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def opening(c, side, party_id, amount="100", *, path="openings"):
    r = await create(
        c,
        "funds/" + path,
        {
            "side": side,
            "party_id": party_id,
            "amount": amount,
            "reason": "逐项核对后录入",
            "refund_balance_confirmed": D(amount) < 0,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def cash_body(side, party_id, allocations, *, kind="SETTLEMENT"):
    return {
        "side": side,
        "party_id": party_id,
        "kind": kind,
        "business_date": "2026-01-02",
        "method": "BANK_TRANSFER",
        "external_reference": "本地纸质参考",
        "reason": "已实际收付，非执行银行转账",
        "allocations": [{"source_id": id, "amount": amount} for id, amount in allocations],
    }


async def source(c, id):
    r = await c.get("/api/v1/funds/sources/" + id)
    assert r.status_code == 200, r.text
    return r.json()


async def test_funds_explicit_cutover_and_zero_summary(catalog_client):
    c = catalog_client
    assert (await c.get("/api/v1/funds/settings")).json()["enabled"] is False
    empty = await c.get("/api/v1/funds/summary?side=AR")
    assert empty.status_code == 200, empty.text
    assert D(empty.json()["balance"]) == 0
    p = await party(c, "AR")
    before = await create(
        c, "funds/openings", {"side": "AR", "party_id": p, "amount": "10", "reason": "未启用"}
    )
    assert before.status_code == 409 and before.json()["code"] == "FUNDS_NOT_ENABLED"
    key = uuid4().hex
    first = await activate(c, key)
    assert (await activate(c, key)).json() == first.json()
    again = await create(c, "funds/activate", {"business_date": "2026-01-02", "reason": "改变切点"})
    assert again.status_code == 409 and again.json()["code"] == "FUNDS_ALREADY_ENABLED"
    config = (await c.get("/api/v1/funds/settings")).json()
    assert config["enabled"] and config["business_date"] == "2026-01-01"


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_partial_multi_source_preview_full_settlement_and_cash_reversal(catalog_client, side):
    c = catalog_client
    await activate(c)
    p = await party(c, side)
    a = await opening(c, side, p, "100.0001")
    b = await opening(c, side, p, "40.0002")
    body = cash_body(side, p, [(a, "20.0001"), (b, "10.0002")])
    preview = await c.post("/api/v1/funds/cash/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert D(preview.json()["amount"]) == D("30.0003")
    assert D((await source(c, a))["balance"]) == D("100.0001")
    posted = await create(c, "funds/cash", body)
    assert posted.status_code == 201, posted.text
    cash = (await c.get("/api/v1/funds/cash/" + posted.json()["id"])).json()
    assert D(cash["amount"]) == D("30.0003") and len(cash["allocations"]) == 2
    assert cash["business_date"] == "2026-01-02"
    partial = await source(c, a)
    assert partial["status"] == "OPEN" and partial["settlement_status"] == "PARTIAL"
    assert D(partial["settlement_amount"]) == 80
    final = await create(c, "funds/cash", cash_body(side, p, [(a, "80"), (b, "30")]))
    assert final.status_code == 201, final.text
    assert (await source(c, a))["settlement_status"] == "PAID"
    early = await create(
        c, "funds/cash/" + posted.json()["id"] + "/reverse", {"reason": "有后续依赖"}
    )
    assert early.status_code == 409 and early.json()["code"] == "FUNDS_REVERSAL_DEPENDENCY"
    reversed_ = await create(
        c, "funds/cash/" + final.json()["id"] + "/reverse", {"reason": "登记有误"}
    )
    assert reversed_.status_code == 200, reversed_.text
    assert D((await source(c, b))["balance"]) == 30
    listing = await c.get("/api/v1/funds/cash", params={"side": side, "party_id": p})
    assert listing.json()["total"] == 2
    assert {x["status"] for x in listing.json()["items"]} == {"POSTED", "REVERSED"}
    summary = (await c.get("/api/v1/funds/summary", params={"side": side, "party_id": p})).json()
    assert D(summary["source_amount"]) == D("140.0003")
    assert D(summary["settled_amount"]) == D("30.0003") and D(summary["balance"]) == 110


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_explicit_negative_opening_partial_refund_and_corrections(catalog_client, side):
    c = catalog_client
    await activate(c)
    p = await party(c, side)
    refused = await create(
        c, "funds/openings", {"side": side, "party_id": p, "amount": "-30", "reason": "未确认性质"}
    )
    assert refused.status_code == 422
    id = await opening(c, side, p, "-30.0011")
    assert (await source(c, id))["status"] == "REFUND"
    wrong_direction = await create(c, "funds/cash", cash_body(side, p, [(id, "1")]))
    assert wrong_direction.status_code == 409
    refund = await create(c, "funds/cash", cash_body(side, p, [(id, "10.0011")], kind="REFUND"))
    assert refund.status_code == 201, refund.text
    assert D((await source(c, id))["refund_amount"]) == 20
    blocked = await create(c, f"funds/sources/{id}/reverse", {"reason": "仍有退款"})
    assert blocked.status_code == 409
    reverse = await create(
        c, "funds/cash/" + refund.json()["id"] + "/reverse", {"reason": "退款误记"}
    )
    assert reverse.status_code == 200, reverse.text
    void = await create(c, f"funds/sources/{id}/reverse", {"reason": "期初错误，冲销后重录"})
    assert void.status_code == 200, void.text
    history = await source(c, id)
    assert history["status"] == "REVERSED" and D(history["balance"]) == 0
    assert len(history["entries"]) == 2 and len(history["cash"]) == 1
    corrected = await opening(c, side, p, "-20.0011", path="adjustments")
    assert (await source(c, corrected))["kind"] == "ADJUSTMENT"
    assert (
        await create(c, f"funds/sources/{corrected}/reverse", {"reason": "调整录入有误"})
    ).status_code == 200


@pytest.mark.parametrize(
    "amount", [0.1, "NaN", "Infinity", "0.00001", "10000000000000000", "-10000000000000000", "0"]
)
async def test_invalid_financial_amount_is_not_rounded_or_accepted(catalog_client, amount):
    c = catalog_client
    await activate(c)
    p = await party(c, "AR")
    r = await create(
        c,
        "funds/openings",
        {
            "side": "AR",
            "party_id": p,
            "amount": amount,
            "reason": "非法精度或范围",
            "refund_balance_confirmed": True,
        },
    )
    assert r.status_code == 422, r.text
    assert (await c.get("/api/v1/funds/sources?side=AR")).json()["total"] == 0


async def test_funds_filters_pagination_and_literal_search(catalog_client):
    c = catalog_client
    await activate(c)
    p = await party(c, "AR")
    for amount in ["10", "20", "-5"]:
        await opening(c, "AR", p, amount)
    other = await party(c, "AR")
    await opening(c, "AR", other, "99")
    page = await c.get(
        "/api/v1/funds/sources", params={"side": "AR", "party_id": p, "page_size": 1, "page": 2}
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 3 and len(page.json()["items"]) == 1
    beyond = await c.get("/api/v1/funds/sources", params={"side": "AR", "party_id": p, "page": 9})
    assert beyond.json()["total"] == 3 and beyond.json()["items"] == []
    refunds = (
        await c.get("/api/v1/funds/sources", params={"side": "AR", "status": "REFUND"})
    ).json()
    assert refunds["total"] == 1 and D(refunds["items"][0]["refund_amount"]) == 5
    parties = await c.get("/api/v1/funds/parties", params={"side": "AR", "q": "_%\\"})
    assert parties.status_code == 200, parties.text
    assert parties.json()["total"] == 2
    empty = await c.get("/api/v1/funds/parties", params={"side": "AP"})
    assert empty.json()["total"] == 0


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_legacy_pending_refund_moves_negative_opening_and_return_reversal_clears_it(
    catalog_client, sale, side
):
    c = catalog_client
    flow = await trade(c, sale, side)
    old_return, posted = await returned(c, flow)
    assert posted.status_code == 200, posted.text
    await activate(c)
    donor = await opening(c, side, flow["party"], "-15")
    positive = await opening(c, side, flow["party"], "20")
    body = {
        "side": side,
        "source_document_id": flow["doc"]["id"],
        "opening_source_id": donor,
        "amount": "-15",
        "reason": "历史原单45已付，已退15仍待退款",
    }
    unconfirmed = await create(c, "funds/legacy-bindings", body)
    assert (
        unconfirmed.status_code == 422
        and unconfirmed.json()["code"] == "REFUND_BALANCE_CONFIRMATION_REQUIRED"
    )
    confirmed = body | {"refund_balance_confirmed": True}
    over = await create(c, "funds/legacy-bindings", confirmed | {"amount": "-15.0001"})
    assert over.status_code == 409 and over.json()["code"] == "LEGACY_AMOUNT_EXCEEDED"
    wrong = await create(c, "funds/legacy-bindings", confirmed | {"opening_source_id": positive})
    assert wrong.status_code == 409 and wrong.json()["code"] == "OPENING_BALANCE_EXCEEDED"
    key = uuid4().hex
    mapped = await create(c, "funds/legacy-bindings", confirmed, key)
    assert mapped.status_code == 201, mapped.text
    assert (await create(c, "funds/legacy-bindings", confirmed, key)).json() == mapped.json()
    id = mapped.json()["id"]
    assert D((await source(c, donor))["balance"]) == 0
    assert D((await source(c, id))["refund_amount"]) == 15
    summary = (
        await c.get("/api/v1/funds/summary", params={"side": side, "party_id": flow["party"]})
    ).json()
    assert D(summary["balance"]) == 5 and D(summary["refund_amount"]) == 15
    reversed_ = await create(
        c,
        f"{flow['prefix']}/documents/{old_return['id']}/reverse",
        {"expected_version": posted.json()["version"], "reason": "历史退货错误，本来没有退货"},
    )
    assert reversed_.status_code == 200, reversed_.text
    assert D((await source(c, id))["balance"]) == 0
    summary = (
        await c.get("/api/v1/funds/summary", params={"side": side, "party_id": flow["party"]})
    ).json()
    assert D(summary["balance"]) == 20 and D(summary["refund_amount"]) == 0
