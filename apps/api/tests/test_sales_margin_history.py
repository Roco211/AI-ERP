"""Sales S3 realized values and historical snapshots through real application commands."""

from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_purchasing import action as purchase_action
from test_sales_orders import action, detail, opening, so
from test_sales_orders import sale as sale
from test_sales_pricing import price, quote
from test_sales_shipments import post_shipment, restock, shipment, stock_document

from forge_erp.core.config import settings


async def return_draft(c, original, qty="1"):
    source = await stock_document(c, original)
    response = await create(
        c,
        "sales/returns",
        {
            "source_id": original["id"],
            "reason": "原销售出库退回",
            "lines": [{"source_line_id": source["lines"][0]["id"], "qty": qty}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def reverse(c, row):
    response = await create(
        c,
        "sales/documents/" + row["id"] + "/reverse",
        {"expected_version": row["version"], "reason": "纠正本次销售单据"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def history(c, **params):
    response = await c.get("/api/v1/sales/price-history", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def amounts(row, **expected):
    for name, value in expected.items():
        assert D(row[name]) == D(str(value)), (name, row)


def set_permissions(identity, permissions):
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text("DELETE FROM forge.role_permissions WHERE organization_id=:org AND role_id=:role"),
            identity,
        )
        for permission in permissions:
            db.execute(
                text("INSERT INTO forge.role_permissions VALUES (:org,:role,:permission)"),
                identity | {"permission": permission},
            )


async def test_realized_margin_excludes_drafts_and_returns_do_not_reopen_fulfillment(
    catalog_client, sale
):
    c = catalog_client
    sale = sale | {"lines": [sale["lines"][0] | {"qty": "100"}]}
    await opening(c, sale, "200")
    order = await so(c, sale)
    amounts(await detail(c, order), shipment_amount=0, shipment_cost=0, gross_margin=0)
    order = (await action(c, order)).json()
    shipped, _ = await shipment(c, order, "60")
    draft = await stock_document(c, shipped)
    assert "actual_cost" not in draft and "gross_margin" not in draft
    amounts(await detail(c, order), shipment_amount=0, shipment_cost=0, gross_margin=0)
    assert (await history(c))["total"] == 0
    assert (await post_shipment(c, shipped)).status_code == 200
    returned = await return_draft(c, shipped, "10")
    draft = await stock_document(c, returned)
    assert draft["kind"] == "RETURN" and draft["original_document_id"] == shipped["id"]
    assert "gross_margin" not in draft and "actual_cost" not in draft
    amounts(draft["lines"][0], amount=150, return_cost=110)
    amounts(await detail(c, order), shipment_amount=900, shipment_cost=660, gross_margin=240)
    assert (await post_shipment(c, returned)).status_code == 200
    info = await detail(c, order)
    amounts(
        info,
        shipment_amount=900,
        shipment_cost=660,
        return_amount=150,
        return_cost=110,
        net_sales_amount=750,
        net_cost=550,
        gross_margin=200,
    )
    amounts(
        info["lines"][0],
        shipped_base_qty=60,
        returned_base_qty=10,
        remaining_base_qty=40,
        reserved_base_qty=40,
    )
    shipped_info = await stock_document(c, shipped)
    returned_info = await stock_document(c, returned)
    amounts(shipped_info, amount=900, actual_cost=660, gross_margin=240)
    amounts(returned_info, amount=150, actual_cost=110, gross_margin=-40)
    amounts(returned_info["lines"][0], actual_cost=110, gross_margin=-40)
    amounts(
        shipped_info["lines"][0],
        returned_qty=10,
        returned_base_qty=10,
        returned_amount=150,
        returned_cost=110,
        returnable_qty=50,
        returnable_base_qty=50,
    )
    closed = await action(c, order, "close", "未发部分取消履约")
    assert closed.status_code == 200, closed.text
    info = await detail(c, order)
    assert info["status"] == "CLOSED" and info["fulfillment_status"] == "PARTIAL"
    amounts(info, gross_margin=200)
    amounts(info["lines"][0], reserved_base_qty=0, remaining_base_qty=40)
    history_row = (await history(c))["items"][0]
    amounts(history_row, returned_qty=10, returned_base_qty=10, returned_amount=150)
    assert all(k not in history_row for k in ("actual_cost", "return_cost", "gross_margin"))
    returns = await c.get("/api/v1/sales/documents", params={"kind": "RETURN"})
    assert returns.status_code == 200 and returns.json()["total"] == 1
    assert returns.json()["items"][0]["id"] == returned["id"]


async def test_return_reversal_restores_current_margin_and_history_return_allowance(
    catalog_client, sale
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    shipped, _ = await shipment(c, order)
    assert (await post_shipment(c, shipped)).status_code == 200
    returned = await return_draft(c, shipped)
    posted = await post_shipment(c, returned)
    assert posted.status_code == 200, posted.text
    amounts(await detail(c, order), gross_margin=8)
    await reverse(c, posted.json())
    info = await stock_document(c, returned)
    assert info["status"] == "REVERSED" and "gross_margin" not in info
    assert "gross_margin" not in info["lines"][0]
    amounts(info, actual_cost=11)
    amounts(await detail(c, order), return_amount=0, return_cost=0, gross_margin=12)
    amounts(
        (await stock_document(c, shipped))["lines"][0],
        returned_qty=0,
        returned_cost=0,
        returnable_qty=3,
    )
    amounts((await history(c))["items"][0], returned_qty=0, returned_amount=0)


async def test_shipment_reversal_excludes_history_margin_and_restores_reservations(
    catalog_client, sale
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    shipped, _ = await shipment(c, order)
    posted = await post_shipment(c, shipped)
    assert posted.status_code == 200, posted.text
    await reverse(c, posted.json())
    assert (await history(c))["total"] == 0
    info = await detail(c, order)
    amounts(info, shipment_amount=0, shipment_cost=0, gross_margin=0)
    amounts(info["lines"][0], reserved_base_qty=7, shipped_base_qty=0)
    info = await stock_document(c, shipped)
    amounts(info, actual_cost=33, amount=45)
    assert "gross_margin" not in info and "gross_margin" not in info["lines"][0]


async def test_full_return_retains_real_transaction_price_and_clears_both_money_tails(
    catalog_client, sale
):
    c = catalog_client
    sale = sale | {"lines": [sale["lines"][0] | {"qty": "3", "unit_price": "0.666667"}]}
    receipt = await restock(c, sale, "3", "0.333333")
    assert (await purchase_action(c, receipt, "post", document=True)).status_code == 200
    await price(c, sale, "standard", "9")
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    shipped, _ = await shipment(c, order, "3")
    assert (await post_shipment(c, shipped)).status_code == 200
    for _ in range(3):
        returned = await return_draft(c, shipped)
        posted = await post_shipment(c, returned)
        assert posted.status_code == 200, posted.text
    amounts(
        await detail(c, order),
        shipment_amount=2,
        shipment_cost=1,
        return_amount=2,
        return_cost=1,
        net_sales_amount=0,
        net_cost=0,
        gross_margin=0,
    )
    amounts(
        (await stock_document(c, shipped))["lines"][0],
        returned_qty=3,
        returned_amount=2,
        returned_cost=1,
        returnable_qty=0,
    )
    result = await history(c)
    assert result["total"] == 1
    amounts(result["items"][0], unit_price="0.666667", amount=2, returned_qty=3, returned_amount=2)
    quotation = (await quote(c, sale)).json()
    assert quotation["price_source"]["source"] == "history"
    amounts(quotation, unit_price="0.666667")


async def test_negative_margin_is_reported_without_changing_actual_cost(catalog_client, sale):
    c = catalog_client
    sale = sale | {"lines": [sale["lines"][0] | {"unit_price": "0"}]}
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    shipped, _ = await shipment(c, order)
    assert (await post_shipment(c, shipped)).status_code == 200
    amounts(await detail(c, order), shipment_amount=0, shipment_cost=33, gross_margin=-33)
    amounts(await stock_document(c, shipped), gross_margin=-33)
    returned = await return_draft(c, shipped)
    assert (await post_shipment(c, returned)).status_code == 200
    amounts(await stock_document(c, returned), gross_margin=11)
    amounts(await detail(c, order), net_sales_amount=0, net_cost=22, gross_margin=-22)


@pytest.mark.parametrize(
    "permissions",
    [
        frozenset({"sales.read"}),
        frozenset({"sales.read", "product.cost.read"}),
        frozenset({"sales.read", "product.price.read"}),
    ],
)
async def test_all_sales_read_surfaces_omit_unauthorized_price_cost_and_margin(
    catalog_client, sale, identities, permissions
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    shipped, _ = await shipment(c, order)
    assert (await post_shipment(c, shipped)).status_code == 200
    returned = await return_draft(c, shipped)
    assert (await post_shipment(c, returned)).status_code == 200
    pending = await return_draft(c, shipped)
    set_permissions(identities[0], permissions)
    cost_fields = {
        "actual_cost",
        "return_cost",
        "returned_cost",
        "gross_margin",
        "net_cost",
        "shipment_cost",
        "inventory_value_delta",
        "input_unit_cost",
    }
    price_fields = {
        "unit_price",
        "amount",
        "returned_amount",
        "price_source",
        "pricing_mode",
        "shipment_amount",
        "return_amount",
        "net_sales_amount",
    }

    def check(value):
        if isinstance(value, dict):
            assert not cost_fields.intersection(value), value
            if "product.price.read" not in permissions:
                assert not price_fields.intersection(value), value
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    paths = [
        "orders",
        "orders/" + order["id"],
        "documents",
        "documents/" + shipped["id"],
        "documents/" + returned["id"],
        "documents/" + pending["id"],
    ]
    for path in paths:
        response = await c.get("/api/v1/sales/" + path)
        assert response.status_code == 200, response.text
        check(response.json())
    response = await c.get("/api/v1/sales/price-history")
    if "product.price.read" in permissions:
        assert response.status_code == 200
        check(response.json())
        amounts(await detail(c, order), shipment_amount=45, return_amount=15, net_sales_amount=30)
    else:
        assert response.status_code == 403
    set_permissions(identities[0], {"product.price.read", "product.cost.read"})
    for path in paths + ["price-history"]:
        assert (await c.get("/api/v1/sales/" + path)).status_code == 403


async def test_history_pagination_filters_and_tenant_boundaries(catalog_client, sale, identities):
    c = catalog_client
    await opening(c, sale, "30")
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    for qty in ("1", "2", "3"):
        shipped, _ = await shipment(c, order, qty)
        assert (await post_shipment(c, shipped)).status_code == 200
    other_customer = (
        await create(c, "customers", {"code": "HIST-OTHER", "name": "另一客户"})
    ).json()
    other_order = await so(c, sale | {"customer_id": other_customer["id"]})
    assert (await action(c, other_order)).status_code == 200
    other_ship, _ = await shipment(c, other_order)
    assert (await post_shipment(c, other_ship)).status_code == 200
    all_rows = (await history(c))["items"]
    assert len(all_rows) == 4
    keys = [(x["posted_at"], x["document_id"], x["line_id"]) for x in all_rows]
    assert keys == sorted(keys, reverse=True)
    first = await history(c, page_size=2)
    second = await history(c, page_size=2, page=2)
    assert first["total"] == second["total"] == 4
    assert first["items"] + second["items"] == all_rows
    assert (await history(c, customer_id=sale["customer_id"]))["total"] == 3
    assert (await history(c, product_id=sale["lines"][0]["product_id"]))["total"] == 4
    assert (await history(c, product_id=str(uuid4())))["total"] == 0
    assert (await history(c, customer_id=str(uuid4())))["total"] == 0
    for params in ({"page_size": 101}, {"page": 0}, {"product_id": "invalid"}):
        assert (await c.get("/api/v1/sales/price-history", params=params)).status_code == 422
    login = await c.post(
        "/api/v1/auth/login",
        json={
            "organization_code": identities[1]["code"],
            "email": "same@example.test",
            "password": "test-only-password-8472",
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert login.status_code == 200
    assert (await history(c))["total"] == 0
    assert (await history(c, customer_id=sale["customer_id"]))["total"] == 0
    assert (await c.get("/api/v1/sales/orders/" + order["id"])).status_code == 404
    assert (await c.get("/api/v1/sales/documents/" + shipped["id"])).status_code == 404


@pytest.mark.parametrize("intervention", ["return", "reverse"])
async def test_history_reads_revalidate_after_concurrent_return_or_shipment_reversal(
    catalog_client, sale, monkeypatch, intervention
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    shipped, _ = await shipment(c, order)
    posted = await post_shipment(c, shipped)
    assert posted.status_code == 200, posted.text
    returned = await return_draft(c, shipped)
    original = AsyncSession.execute
    intervened = False

    async def commit_before_read_lock(self, statement, *args, **kwargs):
        nonlocal intervened
        result = await original(self, statement, *args, **kwargs)
        if "SELECT l.id,sd.order_id" in str(statement) and not intervened:
            intervened = True
            if intervention == "return":
                response = await post_shipment(c, returned)
                assert response.status_code == 200, response.text
            else:
                await reverse(c, posted.json())
        return result

    monkeypatch.setattr(AsyncSession, "execute", commit_before_read_lock)
    result = await history(c)
    assert intervened
    if intervention == "return":
        assert result["total"] == 1
        amounts(result["items"][0], returned_qty=1, returned_amount=15)
    else:
        assert result["total"] == 0 and result["items"] == []


async def test_real_history_preserves_factor_labels_and_quotes_without_intermediate_rounding(
    catalog_client, sale, identities
):
    c = catalog_client
    await opening(c, sale, "20")
    box = (await create(c, "units", {"code": "HIST-BOX", "name": "原七件盒"})).json()
    conversion = (
        await create(
            c,
            "product-units",
            {
                "product_id": sale["lines"][0]["product_id"],
                "unit_id": box["id"],
                "unit_to_base_factor": "7",
            },
        )
    ).json()
    frozen = sale | {
        "lines": [
            sale["lines"][0]
            | {
                "unit_id": box["id"],
                "qty": "1",
                "unit_price": "1",
            }
        ]
    }
    order = await so(c, frozen)
    assert (await action(c, order)).status_code == 200
    shipped, _ = await shipment(c, order, "1")
    assert (await post_shipment(c, shipped)).status_code == 200
    original = (await history(c))["items"][0]
    changed = await c.put(
        "/api/v1/product-units/" + conversion["id"],
        headers={"Idempotency-Key": uuid4().hex},
        json={
            "product_id": sale["lines"][0]["product_id"],
            "unit_id": box["id"],
            "unit_to_base_factor": "1000",
            "expected_version": conversion["version"],
        },
    )
    assert changed.status_code == 200, changed.text
    quotation = (await quote(c, sale, box["id"])).json()
    amounts(quotation, unit_price="142.857143")
    assert quotation["price_source"]["source"] == "history"
    amounts(quotation["price_source"], original_factor=7, target_factor=1000, original_price=1)
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text("UPDATE forge.products SET name='改名停用商品',active=false WHERE id=:id"),
            {"id": sale["lines"][0]["product_id"]},
        )
        db.execute(
            text("UPDATE forge.units SET name='改名停用单位',active=false WHERE id=:id"),
            {"id": box["id"]},
        )
        db.execute(
            text("UPDATE forge.customers SET name='改名停用客户',active=false WHERE id=:id"),
            {"id": sale["customer_id"]},
        )
    assert (await history(c))["items"][0] == original
    amounts(original, unit_to_base_factor=7, base_qty=7, unit_price=1, conversion_version=1)
    assert original["unit_label"] == "原七件盒" and original["customer_name"] == "销售客户"


async def test_realized_margin_remains_frozen_after_later_purchase_average_changes(
    catalog_client, sale
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    first_receipt = await restock(c, sale, "10", "15")
    assert (await purchase_action(c, first_receipt, "post", document=True)).status_code == 200
    shipped, _ = await shipment(c, order)
    assert (await post_shipment(c, shipped)).status_code == 200
    before = await stock_document(c, shipped)
    amounts(before, amount=45, actual_cost=39, gross_margin=6)
    amounts(before["lines"][0], gross_margin=6)
    later_receipt = await restock(c, sale, "10", "30")
    assert (await purchase_action(c, later_receipt, "post", document=True)).status_code == 200
    assert await stock_document(c, shipped) == before
    amounts(
        await detail(c, order), shipment_amount=45, shipment_cost=39, net_cost=39, gross_margin=6
    )


async def test_actual_history_beats_customer_tier_and_quote_rejects_foreign_tenant(
    catalog_client, sale, identities
):
    c = catalog_client
    await opening(c, sale)
    await price(c, sale, "standard", "15")
    await price(c, sale, "wholesale", "13")
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text("UPDATE forge.customers SET price_tier='wholesale' WHERE id=:id"),
            {"id": sale["customer_id"]},
        )
    initial_quote = (await quote(c, sale)).json()
    amounts(initial_quote, unit_price=13)
    assert initial_quote["price_source"]["source"] == "wholesale"
    order = await so(c, sale | {"lines": [sale["lines"][0] | {"unit_price": "11"}]})
    assert (await action(c, order)).status_code == 200
    shipped, _ = await shipment(c, order)
    assert (await post_shipment(c, shipped)).status_code == 200
    after_quote = (await quote(c, sale)).json()
    amounts(after_quote, unit_price=11)
    assert after_quote["price_source"]["source"] == "history"
    special = await price(c, sale, "customer", "7")
    final_quote = (await quote(c, sale)).json()
    amounts(final_quote, unit_price=7)
    assert final_quote["price_source"]["source_id"] == special["id"]
    login = await c.post(
        "/api/v1/auth/login",
        json={
            "organization_code": identities[1]["code"],
            "email": "same@example.test",
            "password": "test-only-password-8472",
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert login.status_code == 200
    foreign = await quote(c, sale)
    assert foreign.status_code == 409 and foreign.json()["code"] == "INVALID_REFERENCE"
    assert "unit_price" not in foreign.text and "price_source" not in foreign.text
    assert (await history(c, customer_id=sale["customer_id"]))["total"] == 0


@pytest.mark.parametrize("source", ["catalog", "history"])
async def test_positive_real_quote_rounding_to_zero_returns_precision_conflict(
    catalog_client, sale, source
):
    c = catalog_client
    tiny = (await create(c, "units", {"code": "PRECISION-TINY", "name": "精微单位"})).json()
    result = await create(
        c,
        "product-units",
        {
            "product_id": sale["lines"][0]["product_id"],
            "unit_id": tiny["id"],
            "unit_to_base_factor": "0.000001",
        },
    )
    assert result.status_code == 201, result.text
    if source == "catalog":
        await price(c, sale, "standard", "0.000001")
    else:
        await opening(c, sale, "1000")
        box = (await create(c, "units", {"code": "PRECISION-BOX", "name": "大包装"})).json()
        result = await create(
            c,
            "product-units",
            {
                "product_id": sale["lines"][0]["product_id"],
                "unit_id": box["id"],
                "unit_to_base_factor": "1000",
            },
        )
        assert result.status_code == 201, result.text
        order = await so(
            c,
            sale
            | {
                "lines": [
                    sale["lines"][0]
                    | {
                        "unit_id": box["id"],
                        "qty": "1",
                        "unit_price": "0.000001",
                    }
                ]
            },
        )
        assert (await action(c, order)).status_code == 200
        shipped, _ = await shipment(c, order, "1")
        assert (await post_shipment(c, shipped)).status_code == 200
    response = await quote(c, sale, tiny["id"])
    assert response.status_code == 409 and response.json()["code"] == "PRICE_PRECISION_CONFLICT"
    assert "unit_price" not in response.text
