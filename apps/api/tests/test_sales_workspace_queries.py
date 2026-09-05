"""Server-owned workspace quantities, stock permission boundaries and source navigation."""

from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_sales_margin_history import set_permissions
from test_sales_orders import action, detail, opening, so
from test_sales_orders import sale as sale
from test_sales_pricing import quote
from test_sales_returns import return_draft
from test_sales_shipments import post_shipment, shipment

STOCK_FIELDS = {"on_hand_qty", "warehouse_reserved_qty", "available_qty"}


async def packed_order(c, sale):
    await opening(c, sale, "2000")
    box = (await create(c, "units", {"code": "WORKSPACE-BOX", "name": "箱"})).json()
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
    confirmed = await action(c, order)
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json(), conversion


async def test_workspace_frozen_units_supply_exact_partial_shipment_default(catalog_client, sale):
    c = catalog_client
    order, conversion = await packed_order(c, sale)
    changed = await c.put(
        "/api/v1/product-units/" + conversion["id"],
        headers={"Idempotency-Key": uuid4().hex},
        json={
            "product_id": conversion["product_id"],
            "unit_id": conversion["unit_id"],
            "unit_to_base_factor": "800",
            "expected_version": conversion["version"],
        },
    )
    assert changed.status_code == 200, changed.text
    first, _ = await shipment(c, order, "0.123456")
    assert (await post_shipment(c, first)).status_code == 200
    line = (await detail(c, order))["lines"][0]
    assert line["remaining_qty"] == line["executable_qty"] == "1.876544"
    assert D(line["remaining_base_qty"]) == D("1876.544000")
    assert D(line["on_hand_qty"]) == D("1876.544000")
    assert D(line["warehouse_reserved_qty"]) == D("1876.544000")
    assert D(line["available_qty"]) == 0
    # The server value can be submitted unchanged without client unit/Decimal arithmetic.
    second, _ = await shipment(c, order, line["executable_qty"])
    assert (await post_shipment(c, second)).status_code == 200
    line = (await detail(c, order))["lines"][0]
    assert line["remaining_qty"] == line["executable_qty"] == "0.000000"


async def test_workspace_return_and_close_preserve_original_unshipped_quantity(
    catalog_client, sale
):
    c = catalog_client
    order, _ = await packed_order(c, sale)
    shipped, _ = await shipment(c, order, "0.5")
    assert (await post_shipment(c, shipped)).status_code == 200
    returned, _ = await return_draft(c, shipped, "0.1")
    assert (await post_shipment(c, returned)).status_code == 200
    line = (await detail(c, order))["lines"][0]
    assert line["remaining_qty"] == line["executable_qty"] == "1.500000"
    assert D(line["returned_base_qty"]) == 100
    assert D(line["on_hand_qty"]) == 1600
    assert D(line["warehouse_reserved_qty"]) == 1500
    assert D(line["available_qty"]) == 100
    assert (await action(c, order, "close", "未发余量关闭")).status_code == 200
    line = (await detail(c, order))["lines"][0]
    assert line["remaining_qty"] == "1.500000" and line["executable_qty"] == "0.000000"
    assert D(line["reserved_base_qty"]) == D(line["warehouse_reserved_qty"]) == 0
    assert D(line["available_qty"]) == 1600


async def test_workspace_absent_stock_defaults_zero_and_uses_order_warehouse(catalog_client, sale):
    c = catalog_client
    order = await so(c, sale)
    other = (await create(c, "warehouses", {"code": "OTHER-WORKSPACE", "name": "另一仓库"})).json()
    await opening(c, sale | {"warehouse_id": other["id"]}, "400")
    line = (await detail(c, order))["lines"][0]
    assert {line[field] for field in STOCK_FIELDS} == {"0.000000"}
    assert line["remaining_qty"] == "7.000000" and line["executable_qty"] == "0.000000"
    await opening(c, sale, "10")
    confirmed = await action(c, order)
    assert confirmed.status_code == 200, confirmed.text
    line = (await detail(c, order))["lines"][0]
    assert D(line["on_hand_qty"]) == 10
    assert D(line["warehouse_reserved_qty"]) == 7 and D(line["available_qty"]) == 3


@pytest.mark.parametrize(
    "permissions",
    [
        {"sales.read"},
        {"sales.read", "product.cost.read"},
        {"sales.read", "inventory.read"},
        {"sales.read", "inventory.read", "product.price.read"},
    ],
)
async def test_workspace_stock_and_cost_have_independent_permissions(
    catalog_client, sale, identities, permissions
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    set_permissions(identities[0], permissions)
    response = await c.get("/api/v1/sales/orders/" + order["id"])
    assert response.status_code == 200, response.text
    line = response.json()["lines"][0]
    assert line["remaining_qty"] == line["executable_qty"] == "7.000000"
    assert D(line["reserved_base_qty"]) == 7
    assert (STOCK_FIELDS <= line.keys()) == ("inventory.read" in permissions)
    if "inventory.read" not in permissions:
        assert STOCK_FIELDS.isdisjoint(line)
    for field in ("actual_cost", "inventory_value", "avg_unit_cost", "net_cost", "gross_margin"):
        assert field not in response.text
    if "product.price.read" not in permissions:
        assert "unit_price" not in response.text and '"amount"' not in response.text


async def test_workspace_live_balance_values_share_one_snapshot(catalog_client, sale, monkeypatch):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    other = await so(c, sale | {"lines": [sale["lines"][0] | {"qty": "2"}]})
    execute = AsyncSession.execute
    changed = False

    async def interpose(self, statement, *args, **kwargs):
        nonlocal changed
        result = await execute(self, statement, *args, **kwargs)
        if "reserved_qty AS warehouse_reserved_qty" in str(statement) and not changed:
            changed = True
            confirmed = await action(c, other)
            assert confirmed.status_code == 200, confirmed.text
        return result

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", interpose)
        line = (await detail(c, order))["lines"][0]
    assert changed
    assert [
        D(line[field]) for field in ("on_hand_qty", "warehouse_reserved_qty", "available_qty")
    ] == [
        10,
        7,
        3,
    ]
    current = (await detail(c, order))["lines"][0]
    assert D(current["warehouse_reserved_qty"]) == 9 and D(current["available_qty"]) == 1
    assert current["executable_qty"] == line["executable_qty"] == "7.000000"


async def test_workspace_tenant_isolation_for_quantities_and_movement_sources(
    catalog_client, sale, identities
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    mine = await c.get("/api/v1/inventory/movements")
    assert mine.status_code == 200, mine.text
    sources = mine.json()["items"]
    reserved = next(row for row in sources if row["document_type"] == "SALES_RESERVATION")
    assert reserved["sales_order_id"] == order["id"]
    stock = next(row for row in sources if row["document_type"] == "OPENING")
    assert "sales_order_id" not in stock
    other = identities[1]
    logged_in = await create(
        c,
        "auth/login",
        {
            "organization_code": other["code"],
            "email": "same@example.test",
            "password": "test-only-password-8472",
        },
    )
    assert logged_in.status_code == 200, logged_in.text
    assert (await c.get("/api/v1/sales/orders/" + order["id"])).status_code == 404
    movements = await c.get(
        "/api/v1/inventory/movements", params={"document_id": reserved["document_id"]}
    )
    assert movements.status_code == 200 and movements.json()["items"] == []
    assert (await c.get("/api/v1/sales/orders")).json()["items"] == []


async def test_workspace_sales_movement_provenance_respects_sales_read_permission(
    catalog_client, sale, identities
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    shipped, _ = await shipment(c, order, "3")
    assert (await post_shipment(c, shipped)).status_code == 200
    returned, _ = await return_draft(c, shipped, "1")
    assert (await post_shipment(c, returned)).status_code == 200
    response = await c.get("/api/v1/inventory/movements")
    assert response.status_code == 200, response.text
    sales_rows = [
        row for row in response.json()["items"] if row["document_type"].startswith("SALES_")
    ]
    assert {row["document_type"] for row in sales_rows} == {
        "SALES_RESERVATION",
        "SALES_SHIPMENT",
        "SALES_RETURN",
    }
    assert all(row["sales_order_id"] == order["id"] for row in sales_rows)
    set_permissions(identities[0], {"inventory.read"})
    restricted = await c.get("/api/v1/inventory/movements")
    assert restricted.status_code == 200, restricted.text
    assert "sales_order_id" not in restricted.text
    assert all("document_type" in row for row in restricted.json()["items"])
    assert "value_delta" not in restricted.text and "avg_cost" not in restricted.text


async def test_workspace_history_quote_names_source_shipment_and_freezes_provenance(
    catalog_client, sale
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    shipped, _ = await shipment(c, order, "3")
    assert (await post_shipment(c, shipped)).status_code == 200
    quoted = await quote(c, sale)
    assert quoted.status_code == 200, quoted.text
    source = quoted.json()["price_source"]
    assert source["source"] == "history" and source["source_document_id"] == shipped["id"]
    original = await c.get("/api/v1/sales/documents/" + source["source_document_id"])
    assert original.status_code == 200, original.text
    assert original.json()["lines"][0]["id"] == source["source_id"]
    auto = sale | {
        "lines": [
            {key: value for key, value in sale["lines"][0].items() if key != "unit_price"}
            | {"pricing_mode": "AUTO", "qty": "1"}
        ]
    }
    followup = await so(c, auto)
    frozen = (await detail(c, followup))["lines"][0]["price_source"]
    assert frozen == source
    # Previously saved manual snapshots do not acquire a misleading source-document link.
    assert "source_document_id" not in (await detail(c, order))["lines"][0]["price_source"]
