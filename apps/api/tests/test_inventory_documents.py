import asyncio
from decimal import Decimal as D
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_inventory_engine import stock as stock

from forge_erp.core.config import settings
from forge_erp.core.db import sessions, set_tenant
from forge_erp.modules.inventory.application import documents


async def draft(c, stock, kind="openings", qty="100", cost="10", direction="IN", **extra):
    _, key, line, _, target = stock
    async with sessions.begin() as db:
        await set_tenant(db, stock[0].organization_id)
        unit = (
            await db.execute(
                text("SELECT unit_id FROM forge.inventory_document_lines WHERE id=:id"),
                {"id": line},
            )
        ).scalar_one()
    body = {
        "warehouse_id": str(key[0]),
        "reason": "test document",
        "lines": [
            {
                "product_id": str(key[1]),
                "unit_id": str(unit),
                "qty": qty,
                "input_unit_cost": cost,
                "direction": direction,
            }
        ],
    } | extra
    if kind == "transfers":
        body["target_warehouse_id"] = str(target)
    result = await create(c, "inventory/" + kind, body)
    assert result.status_code == 201, result.text
    return result.json(), body


async def command(c, doc, action="post", key=None, **body):
    return await create(
        c,
        "inventory/documents/" + doc["id"] + "/" + action,
        {"expected_version": doc["version"]} | body,
        key,
    )


async def balance(c, stock):
    data = (
        await c.get("/api/v1/inventory/balances", params={"warehouse_id": str(stock[1][0])})
    ).json()
    return data["items"][0]


async def test_opening_post_reverse_and_idempotency(catalog_client, stock):
    c = catalog_client
    doc, body = await draft(c, stock)
    assert D((await balance(c, stock))["on_hand_qty"]) == 0
    key = uuid4().hex
    posted = await command(c, doc, key=key)
    assert posted.status_code == 200, posted.text
    assert (await command(c, doc, key=key)).json() == posted.json()
    assert (await command(c, doc)).json()["code"] == "INVALID_DOCUMENT_STATE"
    b = await balance(c, stock)
    assert (D(b["on_hand_qty"]), D(b["inventory_value"])) == (100, 1000)
    result = await command(c, posted.json(), "reverse", reason="录入错误")
    assert result.status_code == 200, result.text
    assert D((await balance(c, stock))["on_hand_qty"]) == 0
    movements = (await c.get("/api/v1/inventory/movements")).json()["items"]
    assert len(movements) == 2 and movements[0]["original_movement_id"] == movements[1]["id"]
    assert (await command(c, posted.json(), "reverse", reason="again")).status_code == 409
    # Only reversed openings exist: a new valid opening can be posted.
    again, _ = await draft(c, stock)
    assert (await command(c, again)).status_code == 200


async def test_cost_adjustment_and_older_reversal_rejected(catalog_client, stock):
    c = catalog_client
    opening, _ = await draft(c, stock)
    original = (await command(c, opening)).json()
    incoming, _ = await draft(c, stock, "adjustments", cost="12")
    assert (await command(c, incoming)).status_code == 200
    outgoing, _ = await draft(c, stock, "adjustments", qty="50", cost=None, direction="OUT")
    result = await command(c, outgoing)
    assert result.status_code == 200, result.text
    b = await balance(c, stock)
    assert (D(b["on_hand_qty"]), D(b["inventory_value"]), D(b["avg_unit_cost"])) == (150, 1650, 11)
    assert (await command(c, original, "reverse", reason="old")).json()[
        "code"
    ] == "REVERSAL_DEPENDENCY_CONFLICT"
    assert (await command(c, result.json(), "reverse", reason="latest")).status_code == 200
    assert (await command(c, original, "reverse", reason="still old")).status_code == 409


async def test_transfer_value_conservation_and_atomic_failure(catalog_client, stock):
    c = catalog_client
    opening, _ = await draft(c, stock)
    await command(c, opening)
    transfer, _ = await draft(c, stock, "transfers", qty="30", cost=None)
    result = await command(c, transfer)
    assert result.status_code == 200, result.text
    rows = (await c.get("/api/v1/inventory/balances")).json()["items"]
    assert sum(D(x["inventory_value"]) for x in rows) == 1000
    assert sorted(D(x["on_hand_qty"]) for x in rows) == [30, 70]
    assert (await command(c, result.json(), "reverse", reason="退回原仓")).status_code == 200
    too_many, _ = await draft(c, stock, "transfers", qty="101", cost=None)
    assert (await command(c, too_many)).json()["code"] == "INSUFFICIENT_STOCK"
    assert D((await balance(c, stock))["on_hand_qty"]) == 100


async def test_stocktake_baseline_refresh_and_zero_difference(catalog_client, stock):
    c = catalog_client
    opening, _ = await draft(c, stock)
    await command(c, opening)
    count, body = await draft(c, stock, "stocktakes", qty="98")
    adjustment, _ = await draft(c, stock, "adjustments", qty="1")
    await command(c, adjustment)
    stale = await command(c, count)
    assert stale.json()["code"] == "STOCKTAKE_STALE"
    refresh = await create(
        c,
        "inventory/stocktakes/" + count["id"] + "/refresh-baseline",
        {"expected_version": count["version"]},
    )
    assert refresh.status_code == 200, refresh.text
    assert (await command(c, refresh.json())).status_code == 200
    assert D((await balance(c, stock))["on_hand_qty"]) == 98
    no_diff, _ = await draft(c, stock, "stocktakes", qty="98")
    before = (await c.get("/api/v1/inventory/movements")).json()["items"]
    assert (await command(c, no_diff)).status_code == 200
    assert len((await c.get("/api/v1/inventory/movements")).json()["items"]) == len(before)


async def test_document_security_and_immutable_content(catalog_client, stock, identities):
    c = catalog_client
    doc, _ = await draft(c, stock)
    await command(c, doc)
    async with sessions.begin() as db:
        await set_tenant(db, stock[0].organization_id)
        line = (await documents.lines(db, stock[0], UUID(doc["id"])))[0]
    attempts = [
        ("UPDATE forge.inventory_documents SET reason='changed' WHERE id=:id", doc["id"]),
        ("UPDATE forge.inventory_document_lines SET qty=2,base_qty=2 WHERE id=:id", line["id"]),
        ("DELETE FROM forge.inventory_document_lines WHERE id=:id", line["id"]),
    ]
    for sql, id in attempts:
        with pytest.raises(DBAPIError):
            async with sessions.begin() as db:
                await set_tenant(db, stock[0].organization_id)
                await db.execute(text(sql), {"id": id})
    with pytest.raises(DBAPIError) as blocked:
        async with sessions.begin() as db:
            await set_tenant(db, stock[0].organization_id)
            await db.execute(
                text(
                    "INSERT INTO forge.inventory_document_lines "
                    "(organization_id,document_id,line_no,product_id,unit_id,product_label,"
                    "unit_label,qty,unit_to_base_factor,base_qty,conversion_version,direction) "
                    "SELECT organization_id,document_id,2,product_id,unit_id,product_label,"
                    "unit_label,qty,unit_to_base_factor,base_qty,conversion_version,direction "
                    "FROM forge.inventory_document_lines WHERE id=:id"
                ),
                {"id": line["id"]},
            )
    assert getattr(blocked.value.orig, "sqlstate", None) == "23514"
    assert "Only draft lines can change" in str(blocked.value.orig)
    for resource, id in [("products", str(stock[1][1])), ("warehouses", str(stock[1][0]))]:
        r = await create(c, resource + "/" + id + "/deactivate", {"expected_version": 1})
        assert r.json()["code"] == "STOCK_IN_USE"
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions WHERE "
                "organization_id=:org AND permission_code='product.cost.read'"
            ),
            {"org": stock[0].organization_id},
        )
    for path in ("balances", "movements", "documents/" + doc["id"]):
        response = await c.get("/api/v1/inventory/" + path)
        assert response.status_code == 200, response.text
        for name in (
            "inventory_value",
            "avg_unit_cost",
            "input_unit_cost",
            "value_delta",
            "rounding_delta",
        ):
            assert name not in response.text
    assert (await command(c, doc)).status_code == 403


async def test_concurrent_same_document_and_stable_pagination(catalog_client, stock):
    c = catalog_client
    doc, _ = await draft(c, stock)
    results = await asyncio.gather(command(c, doc), command(c, doc))
    assert sorted(x.status_code for x in results) == [200, 409]
    for _ in range(3):
        adjustment, _ = await draft(c, stock, "adjustments", qty="1")
        await command(c, adjustment)
    page = (await c.get("/api/v1/inventory/movements?page_size=2")).json()
    newer, _ = await draft(c, stock, "adjustments", qty="1")
    await command(c, newer)
    next_page = (
        await c.get(
            "/api/v1/inventory/movements", params={"page_size": 2, "cursor": page["next_cursor"]}
        )
    ).json()
    assert len({x["id"] for x in page["items"] + next_page["items"]}) == 4
    assert not next_page.get("next_cursor")
    assert (await c.get("/api/v1/inventory/movements?cursor=broken")).status_code == 422
