"""Operating metrics reconcile with actual commands, dates and tenant permissions."""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_funds_integration import cash, enable, returned, today, trade
from test_funds_integration import source as funds_source
from test_sales_orders import action, opening, so
from test_sales_orders import sale as sale
from test_sales_shipments import post_shipment, shipment

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions
from forge_erp.core.errors import Problem
from forge_erp.modules.reporting.application import queries

BASE = "/api/v1/reporting"


async def overview(c, **params):
    r = await c.get(BASE + "/overview", params=params)
    assert r.status_code == 200, r.text
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["X-Request-ID"]
    return r.json()


async def sources(c, metric, **params):
    r = await c.get(BASE + "/sources", params={"metric": metric, **params})
    assert r.status_code == 200, r.text
    return r.json()


def change_permissions(identity, permissions):
    admin = create_engine(settings().migration_database_url)
    with admin.begin() as db:
        db.execute(text("DELETE FROM forge.role_permissions WHERE organization_id=:org"), identity)
        for permission in permissions | {"profile.read"}:
            db.execute(
                text("INSERT INTO forge.role_permissions VALUES(:org,:role,:permission)"),
                {**identity, "permission": permission},
            )
    admin.dispose()


def document_time(identity, id, timestamp):
    admin = create_engine(settings().migration_database_url)
    with admin.begin() as db:
        assert (
            db.execute(text("SELECT code FROM forge.organizations WHERE id=:org"), identity)
            .scalar_one()
            .startswith("TEST_")
        )
        db.execute(text("SET LOCAL session_replication_role = replica"))
        db.execute(
            text(
                "UPDATE forge.inventory_documents SET posted_at=:at "
                "WHERE organization_id=:org AND id=:id"
            ),
            {**identity, "id": id, "at": timestamp},
        )
    admin.dispose()


@pytest.mark.parametrize(
    "start,end,valid",
    [
        (date(2024, 1, 1), date(2024, 12, 31), True),
        (date(2024, 1, 1), date(2025, 1, 1), False),
        (date(2026, 9, 2), date(2026, 9, 1), False),
        (date(2026, 9, 1), date(2026, 9, 1), True),
        (date.max, date.max, False),
    ],
)
def test_report_range_boundaries(start, end, valid):
    if valid:
        assert queries.report_scope(start, end)["date_from"] == start
    else:
        with pytest.raises(Problem) as error:
            queries.report_scope(start, end)
        assert error.value.code == "INVALID_REPORT_RANGE"


def test_business_timezone_uses_calendar_days_even_at_dst(monkeypatch):
    monkeypatch.setattr(settings(), "business_timezone", "America/New_York")
    scope = queries.report_scope(
        date(2026, 3, 8), date(2026, 3, 8), as_of=datetime(2026, 3, 9, 1, tzinfo=UTC)
    )
    ctx = RuntimeContext(None, None, frozenset(), "clock-test")
    params = queries.scope_params(ctx, scope)
    assert params["start"] == datetime(2026, 3, 8, 5, tzinfo=UTC)
    assert params["end"] == datetime(2026, 3, 9, 4, tzinfo=UTC)
    assert scope["business_today"] == date(2026, 3, 8)


def test_extreme_date_is_a_validation_error_instead_of_internal_overflow(monkeypatch):
    monkeypatch.setattr(settings(), "business_timezone", "Asia/Shanghai")
    scope = queries.report_scope(None, date.min)
    ctx = RuntimeContext(None, None, frozenset(), "extreme-date")
    with pytest.raises(Problem) as error:
        queries.scope_params(ctx, scope)
    assert error.value.code == "INVALID_REPORT_RANGE"


async def test_actual_sales_cost_returns_and_current_balances(catalog_client, sale):
    c = catalog_client
    await enable(c)
    flow = await trade(c, sale, "AR")
    _, posted_return = await returned(c, flow, "1")
    assert posted_return.status_code == 200
    data = await overview(c)
    metrics = data["sales"]
    assert metrics["shipment_count"] == metrics["return_count"] == 1
    for key, expected in {
        "shipment_amount": 45,
        "return_amount": 15,
        "net_sales_amount": 30,
        "shipment_cost": 33,
        "return_cost": 11,
        "net_cost": 22,
        "gross_margin": 8,
    }.items():
        assert D(metrics[key]) == expected
    assert metrics["cost_status"] == "AVAILABLE"
    assert D(data["current_ar"]["settlement_amount"]) == 30
    assert D(data["inventory"]["valuation"]) == 88
    assert D(data["cash_ar"]["settlement_amount"]) == 0
    rows = await sources(c, "sales")
    assert rows["total"] == 2
    assert sum(D(x["signed_amount"]) for x in rows["items"]) == 30
    assert sum(D(x["signed_cost"]) for x in rows["items"]) == 22
    assert sum(D(x["gross_margin"]) for x in rows["items"]) == 8
    assert all(x["order_id"] == flow["order"]["id"] for x in rows["items"])
    assert all("organization_id" not in x for x in rows["items"])
    assert sum(D(x["net_sales_amount"]) for x in metrics["daily"]) == 30
    older = await overview(c, date_from="2020-01-01", date_to="2020-01-02")
    assert older["sales"]["shipment_count"] == 0
    assert D(older["sales"]["net_sales_amount"]) == 0
    assert older["current_ar"] == data["current_ar"]
    assert older["inventory"] == data["inventory"]


async def test_period_filters_each_document_timestamp_and_half_open_edges(
    catalog_client, sale, identities
):
    c = catalog_client
    await enable(c)
    flow = await trade(c, sale, "AR")
    draft_return, result = await returned(c, flow, "1")
    assert result.status_code == 200
    # Asia/Shanghai: Sept 1 begins Aug 31 at 16:00 UTC.
    document_time(identities[0], flow["doc"]["id"], datetime(2026, 8, 31, 16, tzinfo=UTC))
    document_time(identities[0], draft_return["id"], datetime(2026, 9, 1, 16, tzinfo=UTC))
    day_one = await overview(c, date_from="2026-09-01", date_to="2026-09-01")
    day_two = await overview(c, date_from="2026-09-02", date_to="2026-09-02")
    assert D(day_one["sales"]["net_sales_amount"]) == 45
    assert D(day_one["sales"]["net_cost"]) == 33
    assert D(day_two["sales"]["net_sales_amount"]) == -15
    assert D(day_two["sales"]["net_cost"]) == -11
    drill = await sources(c, "sales", date_from="2026-09-02", date_to="2026-09-02")
    assert drill["total"] == 1 and drill["items"][0]["kind"] == "RETURN"
    assert D(day_one["current_ar"]["settlement_amount"]) == 30


@pytest.mark.parametrize("side,suffix", [("AR", "ar"), ("AP", "ap")])
async def test_cash_directions_reversal_and_opening_are_separate(
    catalog_client, sale, side, suffix
):
    c = catalog_client
    await enable(c)
    flow = await trade(c, sale, side)
    posted_cash = await cash(c, flow, "45")
    _, result = await returned(c, flow, "1")
    assert result.status_code == 200
    await cash(c, flow, "15", "REFUND")
    extra_opening = await create(
        c,
        "funds/openings",
        {"side": side, "party_id": flow["party"], "amount": "100", "reason": "非本期现金"},
    )
    assert extra_opening.status_code == 201
    data = await overview(c)
    period = data["cash_" + suffix]
    assert period["integration_status"] == "ACTIVE"
    assert D(period["settlement_amount"]) == 45
    assert D(period["refund_amount"]) == 15
    assert D(period["net_cash_amount"]) == 30
    current = data["current_" + suffix]
    assert D(current["settlement_amount"]) == 100
    rows = await sources(c, "cash_" + suffix)
    assert rows["total"] == 2
    assert sum(D(x["signed_amount"]) for x in rows["items"]) == 30
    # A second independent opening and cash can be reversed without downstream dependencies.
    reversal_cash = await create(
        c,
        "funds/cash",
        {
            "side": side,
            "party_id": flow["party"],
            "kind": "SETTLEMENT",
            "business_date": today(),
            "method": "CASH",
            "reason": "将冲销的实际收付",
            "allocations": [{"source_id": extra_opening.json()["id"], "amount": "25"}],
        },
    )
    assert reversal_cash.status_code == 201
    reversed_cash = await create(
        c, "funds/cash/" + reversal_cash.json()["id"] + "/reverse", {"reason": "撤回错误登记"}
    )
    assert reversed_cash.status_code == 200, reversed_cash.text
    after = await overview(c)
    assert after["cash_" + suffix] == period
    assert after["current_" + suffix] == current
    assert posted_cash["id"] in {x["id"] for x in (await sources(c, "cash_" + suffix))["items"]}


async def test_not_enabled_and_unmapped_history_never_appear_as_known_zero(catalog_client, sale):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    draft, _ = await shipment(c, order, "3")
    assert (await post_shipment(c, draft)).status_code == 200
    before = await overview(c)
    assert before["current_ar"]["integration_status"] == "NOT_ENABLED"
    assert "balance" not in before["current_ar"]
    assert "settlement_amount" not in before["cash_ar"]
    await enable(c)
    after = await overview(c)
    assert after["current_ar"]["integration_status"] == "INCOMPLETE"
    assert after["current_ar"]["unmapped_document_count"] == 1
    assert after["current_ar"]["source_count"] == 0
    assert (await sources(c, "current_ar"))["integration_status"] == "INCOMPLETE"


@pytest.mark.parametrize(
    "extra,price,cost",
    [
        (set(), False, False),
        ({"product.price.read"}, True, False),
        ({"product.cost.read"}, False, False),
        ({"product.price.read", "product.cost.read"}, True, True),
    ],
)
async def test_permission_matrix_hides_amounts_cost_trends_and_sources(
    catalog_client, sale, identities, extra, price, cost
):
    c = catalog_client
    await enable(c)
    await trade(c, sale, "AR")
    change_permissions(identities[0], {"dashboard.read", "sales.read"} | extra)
    data = await overview(c)
    assert set(data) == {
        "as_of",
        "business_today",
        "business_timezone",
        "date_from",
        "date_to",
        "restatement_notice",
        "sales",
    }
    for metrics in [data["sales"], *data["sales"]["daily"]]:
        assert ("net_sales_amount" in metrics) is price
        assert ("net_cost" in metrics) is cost
        assert ("cost_status" in metrics) is cost
    row = (await sources(c, "sales"))["items"][0]
    assert ("amount" in row) is price
    assert ("actual_cost" in row) is cost
    assert ("signed_amount" in row) is price
    assert ("gross_margin" in row) is cost
    for metric in (
        "cash_ar",
        "cash_ap",
        "current_ar",
        "current_ap",
        "inventory",
        "low_stock",
        "replenishment",
    ):
        response = await c.get(BASE + "/sources", params={"metric": metric})
        assert response.status_code == 403, response.text


async def test_dashboard_permission_is_only_an_entry_gate(catalog_client, sale, identities):
    c = catalog_client
    await enable(c)
    await trade(c, sale, "AR")
    change_permissions(identities[0], {"dashboard.read"})
    data = await overview(c)
    assert "sales" not in data and "inventory" not in data and "current_ar" not in data
    change_permissions(identities[0], {"dashboard.read", "inventory.read"})
    data = await overview(c)
    assert data["inventory"]["product_count"] == 1
    assert "valuation" not in data["inventory"]
    assert "valuation" not in (await sources(c, "inventory"))["items"][0]
    change_permissions(identities[0], {"dashboard.read", "funds.ar.read"})
    data = await overview(c)
    assert D(data["current_ar"]["balance"]) == 45
    assert "current_ap" not in data and "sales" not in data
    change_permissions(identities[0], {"sales.read", "product.price.read", "product.cost.read"})
    assert (await c.get(BASE + "/overview")).status_code == 403
    assert (await c.get(BASE + "/sources", params={"metric": "sales"})).status_code == 403


async def test_overview_and_drilldown_tenancy_missing_context_and_no_data(
    catalog_client, sale, identities
):
    c = catalog_client
    await enable(c)
    await trade(c, sale, "AR")
    login = await create(
        c,
        "auth/login",
        {
            "organization_code": identities[1]["code"],
            "email": "same@example.test",
            "password": "test-only-password-8472",
        },
    )
    assert login.status_code == 200
    data = await overview(c)
    assert data["sales"]["shipment_count"] == 0
    assert D(data["sales"]["net_sales_amount"]) == 0
    assert data["inventory"]["product_count"] == 0
    assert data["current_ar"]["integration_status"] == "NOT_ENABLED"
    for metric in ("sales", "cash_ar", "current_ar", "inventory", "low_stock", "replenishment"):
        assert (await sources(c, metric))["items"] == []
    async with sessions.begin() as db:
        # RLS still blocks a forged application context when no DB tenant scope was set.
        ctx = RuntimeContext(
            identities[0]["org"],
            identities[0]["user"],
            frozenset({"dashboard.read", "sales.read", "product.price.read"}),
            "rls",
        )
        data = await queries.overview(db, ctx)
        assert data["sales"]["shipment_count"] == 0
    c.cookies.clear()
    assert (await c.get(BASE + "/overview")).status_code == 401


async def test_readonly_snapshot_stays_consistent_across_concurrent_return(
    catalog_client, sale, monkeypatch
):
    c = catalog_client
    await enable(c)
    flow = await trade(c, sale, "AR")
    read_sales, resume = asyncio.Event(), asyncio.Event()
    original = queries.sales_period
    observed = []

    async def paused(db, ctx, scope):
        result = await original(db, ctx, scope)
        if ctx.request_id == "report-snapshot":
            observed.append((await db.execute(text("SHOW transaction_isolation"))).scalar_one())
            observed.append((await db.execute(text("SHOW transaction_read_only"))).scalar_one())
            read_sales.set()
            await asyncio.wait_for(resume.wait(), 15)
        return result

    monkeypatch.setattr(queries, "sales_period", paused)
    reading = asyncio.create_task(
        c.get(BASE + "/overview", headers={"X-Request-ID": "report-snapshot"})
    )
    try:
        await asyncio.wait_for(read_sales.wait(), 10)
        _, result = await asyncio.wait_for(returned(c, flow, "1"), 10)
        assert result.status_code == 200, result.text
        resume.set()
        response = await asyncio.wait_for(reading, 10)
    finally:
        resume.set()
        await asyncio.gather(reading, return_exceptions=True)
    assert response.status_code == 200, response.text
    old = response.json()
    assert observed == ["repeatable read", "on"]
    assert D(old["sales"]["net_sales_amount"]) == 45
    assert D(old["current_ar"]["balance"]) == 45
    assert D(old["inventory"]["valuation"]) == 77
    new = await overview(c)
    assert D(new["sales"]["net_sales_amount"]) == 30
    assert D(new["current_ar"]["balance"]) == 30
    assert D(new["inventory"]["valuation"]) == 88


async def test_missing_cost_is_unavailable_and_never_becomes_zero_margin(
    catalog_client, sale, identities
):
    c = catalog_client
    await enable(c)
    flow = await trade(c, sale, "AR")
    admin = create_engine(settings().migration_database_url)
    with admin.begin() as db:
        assert identities[0]["code"].startswith("TEST_")
        db.execute(text("SET LOCAL session_replication_role = replica"))
        removed = db.execute(
            text(
                "DELETE FROM forge.inventory_movements "
                "WHERE organization_id=:org AND document_id=:doc "
                "AND operation_id=:doc AND kind='ISSUE' RETURNING id"
            ),
            {"org": identities[0]["org"], "doc": flow["doc"]["id"]},
        ).all()
        assert len(removed) == 1
    admin.dispose()
    data = await overview(c)
    metrics = data["sales"]
    assert metrics["cost_status"] == "MISSING_FACTS"
    assert D(metrics["net_sales_amount"]) == 45
    assert "net_cost" not in metrics and "gross_margin" not in metrics
    row = (await sources(c, "sales"))["items"][0]
    assert row["cost_status"] == "MISSING_FACTS"
    assert "actual_cost" not in row and "gross_margin" not in row


async def test_valid_reversal_restates_sales_and_sources(catalog_client, sale):
    c = catalog_client
    await enable(c)
    flow = await trade(c, sale, "AR")
    before = await overview(c)
    assert D(before["sales"]["net_sales_amount"]) == 45
    result = await create(
        c,
        "sales/documents/" + flow["doc"]["id"] + "/reverse",
        {"expected_version": flow["doc"]["version"], "reason": "核实原单错误，保留冲销事实"},
    )
    assert result.status_code == 200, result.text
    after = await overview(c)
    assert after["sales"]["shipment_count"] == 0
    assert D(after["sales"]["net_sales_amount"]) == 0
    assert D(after["sales"]["net_cost"]) == 0
    assert (await sources(c, "sales"))["total"] == 0
    assert D(after["current_ar"]["balance"]) == 0
    assert "后续合法冲销" in after["restatement_notice"]


async def test_aggregates_do_not_multiply_rows_or_add_queries_per_document(
    catalog_client, sale, monkeypatch
):
    c = catalog_client
    await enable(c)
    await opening(c, sale, "10")
    counts = []
    original = AsyncSession.execute

    async def measure():
        seen = []

        async def observe(db, statement, *args, **kwargs):
            seen.append(str(statement))
            return await original(db, statement, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "execute", observe)
            data = await overview(c)
        counts.append(len(seen))
        return data

    await measure()
    flows = []
    for _ in range(3):
        order = await so(c, sale | {"lines": [sale["lines"][0] | {"qty": "2"}]})
        assert (await action(c, order)).status_code == 200
        for _ in range(2):
            draft, _ = await shipment(c, order, "1")
            posted = await post_shipment(c, draft)
            assert posted.status_code == 200
            flows.append({"doc": posted.json(), "party": sale["customer_id"], "side": "AR"})
        # Release the remaining reservation so the next independent order can be confirmed.
        current = (await c.get("/api/v1/sales/orders/" + order["id"])).json()
        assert (await action(c, current, "close", "释放未履约量")).status_code == 200
    source_rows = [await funds_source(c, flow) for flow in flows[:2]]
    receipt = await create(
        c,
        "funds/cash",
        {
            "side": "AR",
            "party_id": sale["customer_id"],
            "kind": "SETTLEMENT",
            "business_date": today(),
            "method": "CASH",
            "reason": "一笔收款分配两条来源",
            "allocations": [{"source_id": row["id"], "amount": "15"} for row in source_rows],
        },
    )
    assert receipt.status_code == 201, receipt.text
    data = await measure()
    assert counts[0] == counts[1] and counts[1] <= 15
    assert data["sales"]["shipment_count"] == 6
    assert D(data["sales"]["net_sales_amount"]) == 90
    assert D(data["sales"]["net_cost"]) == 66
    assert D(data["cash_ar"]["settlement_amount"]) == 30
    assert D(data["current_ar"]["settlement_amount"]) == 60
    assert (await sources(c, "cash_ar"))["total"] == 1
    first = await sources(c, "sales", page=1, page_size=2)
    second = await sources(c, "sales", page=2, page_size=2)
    assert first["total"] == second["total"] == 6
    assert len(first["items"]) == len(second["items"]) == 2
    assert not {x["id"] for x in first["items"]} & {x["id"] for x in second["items"]}
    assert (await sources(c, "sales", page=20, page_size=2))["items"] == []


async def test_cash_business_date_filters_do_not_filter_current_balances(catalog_client, sale):
    c = catalog_client
    enabled = await create(
        c, "funds/activate", {"business_date": "2026-01-01", "reason": "期初日期"}
    )
    assert enabled.status_code == 201
    flow = await trade(c, sale, "AR")
    source = await funds_source(c, flow)
    for business_date, amount in (("2026-02-01", "10"), ("2026-02-02", "5")):
        result = await create(
            c,
            "funds/cash",
            {
                "side": "AR",
                "party_id": flow["party"],
                "kind": "SETTLEMENT",
                "business_date": business_date,
                "method": "CASH",
                "reason": "历史业务日期实际收款",
                "allocations": [{"source_id": source["id"], "amount": amount}],
            },
        )
        assert result.status_code == 201, result.text
    first = await overview(c, date_from="2026-02-01", date_to="2026-02-01")
    second = await overview(c, date_from="2026-02-02", date_to="2026-02-02")
    assert D(first["cash_ar"]["settlement_amount"]) == 10
    assert D(second["cash_ar"]["settlement_amount"]) == 5
    assert first["current_ar"] == second["current_ar"]
    assert D(first["current_ar"]["balance"]) == 30
    rows = await sources(c, "cash_ar", date_from="2026-02-01", date_to="2026-02-01")
    assert rows["total"] == 1 and D(rows["items"][0]["amount"]) == 10


async def test_api_rejects_unbounded_invalid_ranges_and_pages(catalog_client):
    c = catalog_client
    for params in (
        {"date_from": "2024-01-01", "date_to": "2025-01-01"},
        {"date_from": "2026-09-02", "date_to": "2026-09-01"},
        {"date_from": "invalid"},
    ):
        assert (await c.get(BASE + "/overview", params=params)).status_code == 422
    for params in (
        {"metric": "sales", "page_size": 101},
        {"metric": "sales", "page": 0},
        {"metric": "private"},
    ):
        assert (await c.get(BASE + "/sources", params=params)).status_code == 422
