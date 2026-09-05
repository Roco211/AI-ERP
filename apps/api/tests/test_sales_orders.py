import asyncio
from decimal import Decimal as D
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_catalog import catalog_client as catalog_client
from test_catalog import create, product_fixture

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions, set_tenant
from forge_erp.modules.inventory.application.engine import InventoryEngine
from forge_erp.modules.inventory.domain.values import InventoryError
from forge_erp.modules.sales.domain.values import converted_price, line_amount


@pytest.fixture
async def sale(catalog_client):
    c = catalog_client
    product, _, _, unit = await product_fixture(c)
    customer = (await create(c, "customers", {"code": "SC", "name": "销售客户"})).json()
    warehouse = (await create(c, "warehouses", {"code": "SW", "name": "销售仓"})).json()
    return {
        "customer_id": customer["id"],
        "warehouse_id": warehouse["id"],
        "reason": "销售测试",
        "lines": [
            {
                "product_id": product["id"],
                "unit_id": unit["id"],
                "qty": "7",
                "pricing_mode": "MANUAL",
                "unit_price": "15",
            }
        ],
    }


async def so(c, body):
    r = await create(c, "sales/orders", body)
    assert r.status_code == 201, r.text
    return r.json()


async def action(c, row, name="confirm", reason=None, key=None):
    return await create(
        c,
        "sales/orders/" + row["id"] + "/" + name,
        {"expected_version": row["version"]} | ({"reason": reason} if reason else {}),
        key,
    )


async def detail(c, row):
    r = await c.get("/api/v1/sales/orders/" + row["id"])
    assert r.status_code == 200, r.text
    return r.json()


async def opening(c, body, qty="10"):
    r = await create(
        c,
        "inventory/openings",
        {
            "warehouse_id": body["warehouse_id"],
            "reason": "销售测试期初",
            "lines": [
                {
                    "product_id": x["product_id"],
                    "unit_id": x["unit_id"],
                    "qty": qty,
                    "input_unit_cost": "11",
                }
                for x in body["lines"]
            ],
        },
    )
    assert r.status_code == 201, r.text
    posted = await create(
        c, "inventory/documents/" + r.json()["id"] + "/post", {"expected_version": 1}
    )
    assert posted.status_code == 200, posted.text


async def balance(c, body):
    r = await c.get("/api/v1/inventory/balances", params={"warehouse_id": body["warehouse_id"]})
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_sales_draft_confirm_release_and_replay(catalog_client, sale, identities):
    c = catalog_client
    sale = sale | {"lines": [sale["lines"][0] | {"qty": "100"}]}
    await opening(c, sale, "200")
    before = await balance(c, sale)
    row = await so(c, sale)
    assert await balance(c, sale) == before
    key = uuid4().hex
    confirmed = await action(c, row, key=key)
    assert confirmed.status_code == 200, confirmed.text
    assert (await action(c, row, key=key)).json() == confirmed.json()
    assert (await action(c, row)).status_code == 409
    b = (await balance(c, sale))[0]
    assert [
        D(b[k]) for k in ("on_hand_qty", "reserved_qty", "available_qty", "inventory_value")
    ] == [200, 100, 100, 2200]
    info = await detail(c, row)
    assert info["fulfillment_status"] == "UNFULFILLED"
    assert D(info["lines"][0]["reserved_base_qty"]) == 100
    closed = await action(c, confirmed.json(), "close", "未发不再需要")
    assert closed.status_code == 200, closed.text
    assert D((await balance(c, sale))[0]["reserved_qty"]) == 0
    info = await detail(c, row)
    assert info["status"] == "CLOSED" and info["fulfillment_status"] == "UNFULFILLED"
    assert D(info["lines"][0]["remaining_base_qty"]) == 100
    assert D(info["lines"][0]["executable_base_qty"]) == 0
    assert (await action(c, closed.json(), "close", "重复")).status_code == 409
    ident = identities[0]
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        ctx = RuntimeContext(
            ident["org"],
            ident["user"],
            frozenset({"inventory.reconcile", "product.cost.read"}),
            "sales-reconcile",
        )
        engine = InventoryEngine(db, ctx, "inventory.reconcile")
        await engine.lock([(UUID(sale["warehouse_id"]), UUID(sale["lines"][0]["product_id"]))])
        assert all(not r["differences"] for r in await engine.reconcile())
        moves = (
            (
                await db.execute(
                    text(
                        "SELECT kind,base_qty,value_delta FROM forge.inventory_movements "
                        "ORDER BY sequence"
                    )
                )
            )
            .mappings()
            .all()
        )
        assert [m["kind"] for m in moves] == ["RECEIVE", "RESERVE", "RELEASE"]
        assert all(m["base_qty"] == m["value_delta"] == 0 for m in moves[1:])


async def test_sales_competing_orders_and_duplicate_confirm(catalog_client, sale):
    c = catalog_client
    await opening(c, sale)
    a, b = await so(c, sale), await so(c, sale)
    results = await asyncio.gather(action(c, a), action(c, b))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert next(r for r in results if r.status_code == 409).json()["code"] == "INSUFFICIENT_STOCK"
    assert D((await balance(c, sale))[0]["reserved_qty"]) == 7
    winner = next(r.json() for r in results if r.status_code == 200)
    results = await asyncio.gather(
        action(c, winner, "close", "结束"), action(c, winner, "cancel", "取消")
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    assert D((await balance(c, sale))[0]["reserved_qty"]) == 0


async def test_sales_multiline_shortage_rolls_back_every_fact(catalog_client, sale, identities):
    c = catalog_client
    await opening(c, sale)
    original = (await c.get("/api/v1/products/" + sale["lines"][0]["product_id"])).json()
    product = (
        await create(
            c,
            "products",
            {
                "sku": "SECOND",
                "name": "第二商品",
                "category_id": original["category_id"],
                "base_unit_id": sale["lines"][0]["unit_id"],
                "attributes": {"材质": "304"},
            },
        )
    ).json()
    unit = {"id": sale["lines"][0]["unit_id"]}
    body = sale | {
        "lines": sale["lines"]
        + [
            {
                "product_id": product["id"],
                "unit_id": unit["id"],
                "qty": "1",
                "pricing_mode": "MANUAL",
                "unit_price": "2",
            }
        ]
    }
    row = await so(c, body)
    r = await action(c, row)
    assert r.status_code == 409, r.text
    assert (await detail(c, row))["status"] == "DRAFT"
    assert D((await balance(c, sale))[0]["reserved_qty"]) == 0
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        assert (await db.execute(text("SELECT count(*) FROM forge.sales_documents"))).scalar() == 0
        assert (
            await db.execute(text("SELECT count(*) FROM forge.inventory_reservations"))
        ).scalar() == 0
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.inventory_movements WHERE kind='RESERVE'")
            )
        ).scalar() == 0


async def test_sales_immutable_sources_and_generic_commands(catalog_client, sale, identities):
    c = catalog_client
    await opening(c, sale)
    row = await so(c, sale)
    confirmed = (await action(c, row)).json()
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        doc = str(
            (
                await db.execute(
                    text("SELECT id FROM forge.sales_documents WHERE kind='RESERVATION'")
                )
            ).scalar_one()
        )
    for name in ["post", "reverse"]:
        r = await create(
            c,
            f"inventory/documents/{doc}/{name}",
            {"expected_version": 2} | ({"reason": "绕过"} if name == "reverse" else {}),
        )
        assert r.json()["code"] == "SALES_COMMAND_REQUIRED"
    r = await create(c, f"purchasing/documents/{doc}/post", {"expected_version": 2})
    assert r.status_code == 404
    for sql in [
        "UPDATE forge.sales_orders SET reason='bad',version=version+1 WHERE id=:id",
        "DELETE FROM forge.sales_order_lines WHERE order_id=:id",
        "UPDATE forge.sales_order_lines SET unit_price=0 WHERE order_id=:id",
        "DELETE FROM forge.sales_document_lines WHERE order_id=:id",
    ]:
        with pytest.raises(DBAPIError):
            async with sessions.begin() as db:
                await set_tenant(db, identities[0]["org"])
                await db.execute(text(sql), {"id": row["id"]})
    assert (await action(c, confirmed, "cancel", "取消")).status_code == 200


async def test_sales_tenancy_and_missing_context(catalog_client, sale, identities):
    c = catalog_client
    await opening(c, sale)
    row = await so(c, sale)
    assert (await action(c, row)).status_code == 200
    for org in [identities[1]["org"], None]:
        async with sessions.begin() as db:
            if org:
                await set_tenant(db, org)
            for table in [
                "sales_orders",
                "sales_order_lines",
                "sales_documents",
                "sales_document_lines",
            ]:
                assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar() == 0
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        assert (await db.execute(text("SELECT count(*) FROM forge.sales_orders"))).scalar() == 1
    await c.post("/api/v1/auth/logout", json={})
    login = await c.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": uuid4().hex},
        json={
            "organization_code": identities[1]["code"],
            "email": "same@example.test",
            "password": "test-only-password-8472",
        },
    )
    assert login.status_code == 200, login.text
    assert (await c.get("/api/v1/sales/orders/" + row["id"])).status_code == 404
    assert (await action(c, row)).status_code == 404
    assert (await create(c, "sales/orders", sale)).status_code == 409


async def test_sales_permissions_and_historical_release(catalog_client, sale, identities):
    c = catalog_client
    await opening(c, sale)
    ident = identities[0]
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                "AND permission_code='product.cost.read'"
            ),
            ident,
        )
    row = await so(c, sale)
    key = uuid4().hex
    r = await action(c, row, key=key)
    assert r.status_code == 200, r.text
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                "AND permission_code IN "
                "('product.price.read','sales.order.confirm')"
            ),
            ident,
        )
        for table in ["customers", "units", "products", "warehouses"]:
            db.execute(
                text(f"UPDATE forge.{table} SET active=false WHERE organization_id=:org"), ident
            )
    info = await detail(c, row)
    assert (
        "amount" not in info
        and "unit_price" not in info["lines"][0]
        and "price_source" not in info["lines"][0]
    )
    assert (await action(c, row, key=key)).status_code == 403
    assert (await create(c, "sales/orders", sale)).status_code == 403
    assert (await action(c, r.json(), "close", "历史义务释放")).status_code == 200


async def test_sales_snapshot_conversion_changed(catalog_client, sale):
    c = catalog_client
    await opening(c, sale)
    row = await so(c, sale)
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text("UPDATE forge.product_units SET version=version+1 WHERE product_id=:id"),
            {"id": sale["lines"][0]["product_id"]},
        )
    assert (await action(c, row)).json()["code"] == "UNIT_CONVERSION_CHANGED"
    assert (await action(c, row, "cancel", "换算变化")).status_code == 200


def test_sales_price_arithmetic():
    assert line_amount(D("0.3"), D("0.000167")) == D("0.0001")
    assert converted_price(D("1"), D("3"), D("7")) == D("0.428571")
    assert converted_price(D("0"), D("10")) == 0
    with pytest.raises(InventoryError, match="换算报价"):
        converted_price(D("0.000001"), D("0.000001"))
    for value in [0.1, D("NaN"), D("Infinity"), D("-1")]:
        with pytest.raises(InventoryError):
            line_amount(D("1"), value)


async def test_sales_unrelated_inventory_key_can_confirm_while_other_waits(
    catalog_client, sale, identities
):
    c = catalog_client
    await opening(c, sale)
    wh = (await create(c, "warehouses", {"code": "SW2", "name": "独立仓"})).json()
    other = sale | {"warehouse_id": wh["id"]}
    await opening(c, other)
    a, b = await so(c, sale), await so(c, other)
    pending = None
    try:
        async with sessions.begin() as db:
            await set_tenant(db, identities[0]["org"])
            lock = (
                f"inventory:{identities[0]['org']}:{sale['warehouse_id']}:"
                f"{sale['lines'][0]['product_id']}"
            )
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": lock}
            )
            pending = asyncio.create_task(action(c, a))
            # The first confirmation is blocked on its stock key; a distinct warehouse must proceed.
            second = await asyncio.wait_for(action(c, b), 2)
            assert second.status_code == 200, second.text
            assert not pending.done()
        assert (await asyncio.wait_for(pending, 2)).status_code == 200
    finally:
        if pending and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
