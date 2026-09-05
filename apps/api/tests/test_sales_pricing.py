from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_sales_orders import action, detail, opening, so
from test_sales_orders import sale as sale

from forge_erp.core.config import settings


async def price(c, sale, kind, amount):
    body = {"product_id": sale["lines"][0]["product_id"], "price_type": kind, "price": amount}
    if kind == "customer":
        body["customer_id"] = sale["customer_id"]
    r = await create(c, "product-prices", body)
    assert r.status_code == 201, r.text
    return r.json()


async def quote(c, sale, unit=None):
    return await c.get(
        "/api/v1/sales/price-quote",
        params={
            "customer_id": sale["customer_id"],
            "product_id": sale["lines"][0]["product_id"],
            "unit_id": unit or sale["lines"][0]["unit_id"],
        },
    )


async def test_sales_quote_priority_unset_zero_and_auto_snapshot(catalog_client, sale):
    c = catalog_client
    assert (await quote(c, sale)).json()["price_source"]["source"] == "unset"
    auto = sale | {
        "lines": [
            {k: v for k, v in sale["lines"][0].items() if k not in {"unit_price", "pricing_mode"}}
        ]
    }
    assert (await create(c, "sales/orders", auto)).json()["code"] == "PRICE_UNSET"
    await price(c, sale, "standard", "15")
    q = (await quote(c, sale)).json()
    assert q["price_source"]["source"] == "standard" and D(q["unit_price"]) == 15
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text("UPDATE forge.customers SET price_tier='wholesale' WHERE id=:id"),
            {"id": sale["customer_id"]},
        )
    await price(c, sale, "wholesale", "13")
    assert (await quote(c, sale)).json()["price_source"]["source"] == "wholesale"
    special = await price(c, sale, "customer", "0")
    assert D((await quote(c, sale)).json()["unit_price"]) == 0
    row = await so(c, auto)
    info = await detail(c, row)
    assert D(info["amount"]) == 0 and info["lines"][0]["price_source"]["source_id"] == special["id"]
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text("UPDATE forge.product_prices SET price=20,version=version+1 WHERE id=:id"),
            {"id": special["id"]},
        )
    await opening(c, sale)
    assert (await action(c, row)).status_code == 200
    assert D((await detail(c, row))["amount"]) == 0
    assert D((await quote(c, sale)).json()["unit_price"]) == 20


async def test_sales_quote_unit_conversion_manual_customer_change(catalog_client, sale):
    c = catalog_client
    await price(c, sale, "standard", "1.234567")
    box = (await create(c, "units", {"code": "BOX", "name": "箱"})).json()
    r = await create(
        c,
        "product-units",
        {
            "product_id": sale["lines"][0]["product_id"],
            "unit_id": box["id"],
            "unit_to_base_factor": "1000",
        },
    )
    assert r.status_code == 201, r.text
    q = (await quote(c, sale, box["id"])).json()
    assert D(q["unit_price"]) == D("1234.567") and D(q["price_source"]["target_factor"]) == 1000
    row = await so(c, sale)
    other = (await create(c, "customers", {"code": "SC2", "name": "另一个客户"})).json()
    await price(c, sale, "customer", "9")
    auto = sale | {
        "customer_id": other["id"],
        "expected_version": row["version"],
        "lines": [{"product_id": sale["lines"][0]["product_id"], "unit_id": box["id"], "qty": "2"}],
    }
    saved = await c.put(
        "/api/v1/sales/orders/" + row["id"] + "/draft",
        json=auto,
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert saved.status_code == 200, saved.text
    info = await detail(c, row)
    assert D(info["lines"][0]["base_qty"]) == 2000 and D(info["amount"]) == D("2469.1340")
    assert info["lines"][0]["price_source"]["source"] == "standard"
    stale = await action(c, row)
    assert stale.json()["code"] == "DOCUMENT_VERSION_CONFLICT"


@pytest.mark.parametrize("bad", [0.1, "NaN", "Infinity", "-1", "0.0000001", "100000000000000"])
async def test_sales_invalid_numeric_input(catalog_client, sale, bad):
    body = sale | {"lines": [sale["lines"][0] | {"unit_price": bad}]}
    assert (await create(catalog_client, "sales/orders", body)).status_code == 422


async def test_sales_rejects_forged_origin_and_auto_price(catalog_client, sale):
    c = catalog_client
    for fields in [
        {"organization_id": str(uuid4())},
        {"reservation_id": str(uuid4())},
        {"price_source": {"source": "customer"}},
        {"pricing_mode": "AUTO"},
    ]:
        body = sale | {"lines": [sale["lines"][0] | fields]}
        assert (await create(c, "sales/orders", body)).status_code == 422
    assert (
        await create(c, "sales/orders", sale | {"organization_id": str(uuid4())})
    ).status_code == 422
    assert (await c.get("/api/v1/sales/orders?page_size=101")).status_code == 422


async def test_sales_history_resolver_with_isolated_historical_fixtures(
    catalog_client, sale, identities
):
    """Query fixtures only: does not claim an implemented shipment POST command."""
    c = catalog_client
    await price(c, sale, "standard", "15")
    row = await so(c, sale)
    line = (await detail(c, row))["lines"][0]
    doc, line_id = uuid4(), uuid4()
    with create_engine(settings().migration_database_url).begin() as db:
        params = {
            "org": identities[0]["org"],
            "actor": identities[0]["user"],
            "doc": doc,
            "line": line_id,
            "order": row["id"],
            "orderline": line["id"],
            "wh": sale["warehouse_id"],
            "product": line["product_id"],
            "unit": line["unit_id"],
        }
        # Deliberately seed the already-specified history schema in a disposable test tenant.
        for sql in [
            "INSERT INTO "
            "forge.inventory_documents(id,organization_id,number,type,reason,"
            "warehouse_id,created_by) "
            "VALUES(:doc,:org,'HIST-FIXTURE','SALES_SHIPMENT','历史查询fixture',"
            ":wh,:actor)",
            "INSERT INTO "
            "forge.sales_documents(id,organization_id,order_id,kind) "
            "VALUES(:doc,:org,:order,'SHIPMENT')",
            "INSERT INTO "
            "forge.inventory_document_lines(id,organization_id,document_id,"
            "line_no,product_id,unit_id,product_label,unit_label,qty,"
            "unit_to_base_factor,base_qty,conversion_version,direction) "
            "VALUES(:line,:org,:doc,1,:product,:unit,'历史商品','历史单位',1,7,7,1,"
            "'OUT')",
            "INSERT INTO "
            "forge.sales_document_lines(id,organization_id,document_id,"
            "order_id,order_line_id,unit_price,amount) "
            "VALUES(:line,:org,:doc,:order,:orderline,1,1)",
        ]:
            db.execute(text(sql), params)
    assert (await quote(c, sale)).json()["price_source"]["source"] == "standard"
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "UPDATE forge.inventory_documents SET "
                "status='POSTED',posted_at=now(),version=2 WHERE id=:doc"
            ),
            {"doc": doc},
        )
    q = (await quote(c, sale)).json()
    assert q["price_source"]["source"] == "history" and D(q["unit_price"]) == D("0.142857")
    assert q["price_source"]["source_id"] == str(line_id)
    special = await price(c, sale, "customer", "8")
    assert (await quote(c, sale)).json()["price_source"]["source"] == "customer"
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text("UPDATE forge.product_prices SET active=false WHERE id=:id"), {"id": special["id"]}
        )
        reverse = uuid4()
        db.execute(
            text(
                "INSERT INTO "
                "forge.inventory_reversals(id,organization_id,document_id,reason,"
                "actor_id,request_id) "
                "VALUES(:rev,:org,:doc,'历史冲销fixture',:actor,'fixture')"
            ),
            params | {"rev": reverse},
        )
        db.execute(
            text(
                "UPDATE forge.inventory_documents SET "
                "status='REVERSED',reversed_at=now(),reversal_id=:rev,version=3 "
                "WHERE id=:doc"
            ),
            {"doc": doc, "rev": reverse},
        )
    assert (await quote(c, sale)).json()["price_source"]["source"] == "standard"
