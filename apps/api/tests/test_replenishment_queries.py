import asyncio
from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_purchasing import action as purchase_action
from test_purchasing import po
from test_replenishment import advice, confirmed_body, preview_body, purchase_body
from test_replenishment import replenishment as replenishment
from test_sales_orders import action as sales_action
from test_sales_orders import opening, so
from test_sales_returns import return_draft, reverse
from test_sales_shipments import post_shipment, shipment

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions, set_tenant
from forge_erp.modules.replenishment.application import commands, queries


def posted_at(identity, documents):
    # Only isolated test facts are backdated, to exercise exact business-window boundaries.
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(text("SET LOCAL session_replication_role=replica"))
        for id, at in documents:
            db.execute(
                text(
                    "UPDATE forge.inventory_documents SET posted_at=:at "
                    "WHERE organization_id=:org AND id=:id"
                ),
                {"org": identity["org"], "id": id, "at": at},
            )


async def sales_facts(c, fixture, reserve=True):
    customer = (await create(c, "customers", {"code": "RC", "name": "补货销量客户"})).json()
    body = {
        "customer_id": customer["id"],
        "warehouse_id": fixture["warehouse"]["id"],
        "reason": "补货统计销售",
        "lines": [
            {
                "product_id": fixture["product"]["id"],
                "unit_id": fixture["unit"]["id"],
                "qty": "90",
                "pricing_mode": "MANUAL",
                "unit_price": "15",
            }
        ],
    }
    await opening(c, body, "98")
    order = await so(c, body)
    assert (await sales_action(c, order)).status_code == 200
    shipped, _ = await shipment(c, order, "90")
    posted_ship = await post_shipment(c, shipped)
    assert posted_ship.status_code == 200, posted_ship.text
    returned, _ = await return_draft(c, shipped, "30")
    posted_return = await post_shipment(c, returned)
    assert posted_return.status_code == 200, posted_return.text
    if reserve:
        reservation = await so(c, body | {"lines": [body["lines"][0] | {"qty": "30"}]})
        reserved = await sales_action(c, reservation)
        assert reserved.status_code == 200, reserved.text
    return posted_ship.json(), posted_return.json()


async def test_real_sales_returns_reservations_and_inbound_match_normative_example(
    catalog_client,
    replenishment,
    identities,
):
    c = catalog_client
    shipped, returned = await sales_facts(c, replenishment)
    info = queries.window()
    posted_at(
        identities[0],
        [
            (shipped["id"], info.window_start + timedelta(days=1)),
            (returned["id"], info.window_start + timedelta(days=2)),
        ],
    )
    order = await po(c, purchase_body(replenishment, "6"))
    assert (await purchase_action(c, order)).status_code == 200
    item = (await advice(c))["items"][0]
    expected = {
        "available_qty": 8,
        "open_purchase_qty": 6,
        "shipped_qty": 90,
        "returned_qty": 30,
        "net_sales_qty": 60,
        "daily_sales_qty": 2,
        "reorder_point": 20,
        "target_stock": 34,
        "inventory_position": 14,
        "gap": 20,
        "suggested_base_qty": 20,
        "days_of_stock": 4,
    }
    assert {k: D(item[k]) for k in expected} == expected
    assert (await advice(c, candidate_only=True, suggested_only=True))["total"] == 1
    ctx = RuntimeContext(
        identities[0]["org"], identities[0]["user"], frozenset(queries.READ_PERMISSIONS), "counts"
    )
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        counts = await queries.count_suggestions(db, ctx, info.as_of)
        assert counts.candidate_count == counts.suggested_count == 1
        source = await queries.report_sources(db, ctx, info.as_of, 1, 25)
        assert source["total"] == 1 and source["items"][0]["suggested_base_qty"] == 20
        assert source["items"][0]["min_stock_qty"] == 10
    more = await po(c, purchase_body(replenishment, "24"))
    assert (await purchase_action(c, more)).status_code == 200
    item = (await advice(c))["items"][0]
    assert D(item["available_qty"]) == 8 and D(item["inventory_position"]) == 38
    assert D(item["suggested_base_qty"]) == 0 and "INBOUND_COVERS_TARGET" in item["reasons"]
    assert (await advice(c, candidate_only=True))["total"] == 1
    assert (await advice(c, suggested_only=True))["total"] == 0


async def test_window_excludes_today_uses_each_return_date_and_ignores_reversed_facts(
    catalog_client,
    replenishment,
    identities,
):
    c = catalog_client
    shipped, returned = await sales_facts(c, replenishment, reserve=False)
    info = queries.window()
    # Shipment before the first complete day is outside; return exactly at start is inside.
    posted_at(
        identities[0],
        [
            (shipped["id"], info.window_start - timedelta(microseconds=1)),
            (returned["id"], info.window_start),
        ],
    )
    item = (await advice(c))["items"][0]
    assert D(item["net_sales_qty"]) == -30 and D(item["daily_sales_qty"]) == 0
    assert item["days_of_stock"] is None and "NON_POSITIVE_NET_SALES" in item["reasons"]
    posted_at(
        identities[0], [(shipped["id"], info.window_start), (returned["id"], info.window_end)]
    )
    item = (await advice(c))["items"][0]
    assert D(item["net_sales_qty"]) == 90 and D(item["returned_qty"]) == 0
    # Reverse the last return through its Command; its posted_at stays in the window.
    posted_at(identities[0], [(returned["id"], info.window_start + timedelta(days=1))])
    assert (await reverse(c, returned)).status_code == 200
    item = (await advice(c))["items"][0]
    assert D(item["shipped_qty"]) == 90 and D(item["returned_qty"]) == 0


async def test_organization_inventory_includes_other_warehouse_and_missing_lead_is_not_zero(
    catalog_client,
    replenishment,
):
    c = catalog_client
    await opening(c, purchase_body(replenishment), "3")
    warehouse = (await create(c, "warehouses", {"code": "OTHER", "name": "另一个库存仓"})).json()
    await opening(c, purchase_body(replenishment) | {"warehouse_id": warehouse["id"]}, "4")
    item = (await advice(c))["items"][0]
    assert D(item["available_qty"]) == 7
    sp = replenishment["supplier_product"]
    off = await create(
        c, "supplier-products/" + sp["id"] + "/deactivate", {"expected_version": sp["version"]}
    )
    assert off.status_code == 200, off.text
    item = (await advice(c))["items"][0]
    assert item["lead_days"] is None and "MISSING_LEAD_DAYS" in item["reasons"]


async def test_read_snapshot_remains_consistent_when_stock_commits_after_authentication(
    catalog_client,
    replenishment,
    monkeypatch,
):
    c = catalog_client
    reached, release = asyncio.Event(), asyncio.Event()
    execute = AsyncSession.execute
    paused = False

    async def pause(self, statement, *args, **kwargs):
        nonlocal paused
        if "SELECT paged.*,totals.total" in str(statement) and not paused:
            paused = True
            reached.set()
            await asyncio.wait_for(release.wait(), 10)
        return await execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", pause)
    reading = asyncio.create_task(advice(c))
    await asyncio.wait_for(reached.wait(), 5)
    try:
        await opening(c, purchase_body(replenishment), "4")
    finally:
        release.set()
    before = await asyncio.wait_for(reading, 5)
    assert D(before["items"][0]["available_qty"]) == 0
    assert D((await advice(c))["items"][0]["available_qty"]) == 4


async def test_create_waits_for_changed_conversion_then_rejects_original_confirmation(
    catalog_client,
    replenishment,
    identities,
):
    c = catalog_client
    box = (await create(c, "units", {"code": "LOCKBOX", "name": "锁换算测试箱"})).json()
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
    body, _ = await confirmed_body(c, replenishment, unit_id=box["id"])
    pending = None
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        blocker = (await db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        await db.execute(
            text(
                "UPDATE forge.product_units SET unit_to_base_factor=5,version=version+1 "
                "WHERE id=:id"
            ),
            {"id": conversion["id"]},
        )
        pending = asyncio.create_task(create(c, "replenishment/purchase-orders", body))
        for _ in range(100):
            async with sessions.begin() as inspect:
                blocked = (
                    await inspect.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE :blocker=ANY(pg_blocking_pids(pid))"
                        ),
                        {"blocker": blocker},
                    )
                ).scalar_one()
            if blocked:
                break
            await asyncio.sleep(0.02)
        assert blocked and not pending.done()
    result = await asyncio.wait_for(pending, 5)
    assert result.status_code == 409 and result.json()["code"] == "REPLENISHMENT_PREVIEW_CHANGED"


async def test_create_checks_stock_in_one_fresh_snapshot_after_reference_lock(
    catalog_client,
    replenishment,
    monkeypatch,
):
    c = catalog_client
    body, _ = await confirmed_body(c, replenishment)
    reached, release = asyncio.Event(), asyncio.Event()
    lock = commands.lock_references

    async def pause(*args, **kwargs):
        await lock(*args, **kwargs)
        reached.set()
        await asyncio.wait_for(release.wait(), 10)

    monkeypatch.setattr(commands, "lock_references", pause)
    creating = asyncio.create_task(create(c, "replenishment/purchase-orders", body))
    await asyncio.wait_for(reached.wait(), 5)
    try:
        # A recommendation read/create takes no global inventory lock. Stock can commit;
        # the fresh creation query must then reject the already-confirmed old quantity.
        await opening(c, purchase_body(replenishment), "1")
    finally:
        release.set()
    result = await asyncio.wait_for(creating, 5)
    assert result.status_code == 409 and result.json()["code"] == "REPLENISHMENT_BASIS_CHANGED"


async def test_tenant_injected_fields_and_foreign_references_rejected(
    catalog_client, replenishment, identities
):
    body = await preview_body(catalog_client, replenishment)
    for field in ("organization_id", "actor_id"):
        invalid = body | {field: str(identities[1]["org"])}
        assert (
            await catalog_client.post("/api/v1/replenishment/purchase-preview", json=invalid)
        ).status_code == 422
    for field in ("supplier_id", "warehouse_id"):
        invalid = body | {field: str(uuid4())}
        assert (
            await catalog_client.post("/api/v1/replenishment/purchase-preview", json=invalid)
        ).status_code == 409
