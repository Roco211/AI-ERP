from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_sales_orders import action, balance, detail, opening, so
from test_sales_orders import sale as sale
from test_sales_pricing import price
from test_sales_shipments import post_shipment, shipment

from forge_erp.core.config import settings
from forge_erp.core.db import sessions
from forge_erp.core.errors import Problem
from forge_erp.modules.assistant.application import drafts
from forge_erp.modules.assistant.domain.drafts import DRAFT_INPUT
from forge_erp.modules.identity.application.commands import resolve_context
from forge_erp.modules.sales.application import orders as sales_orders


def input_body(sale, kind="SALES"):
    return DRAFT_INPUT.validate_python({"kind": kind, "order": sale})


@asynccontextmanager
async def transaction(client, *, without=None):
    async with sessions.begin() as db:
        ctx = await resolve_context(db, client.cookies["forge_session"], "assistant-draft-test")
        permissions = ctx.permissions | {"ai.use", "ai.draft.create"}
        ctx = replace(ctx, permissions=permissions - ({without} if without else set()))
        yield db, ctx


async def preview(client, body):
    async with transaction(client) as tx:
        return await drafts.preview_draft(*tx, body)


async def counts(db):
    return {
        table: (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar_one()
        for table in (
            "sales_orders",
            "sales_order_lines",
            "purchase_orders",
            "purchase_order_lines",
            "inventory_movements",
            "inventory_reservations",
            "inventory_documents",
            "funds_entries",
            "funds_cash_documents",
            "audit_events",
            "outbox_events",
            "idempotency_keys",
        )
    }


def minimal_order(kind="SALES"):
    line = {
        "product_id": str(uuid4()),
        "unit_id": str(uuid4()),
        "qty": "1",
        "unit_price": "2",
    }
    if kind == "SALES":
        line["pricing_mode"] = "MANUAL"
    return {
        "customer_id" if kind == "SALES" else "supplier_id": str(uuid4()),
        "warehouse_id": str(uuid4()),
        "reason": "测试",
        "lines": [line],
    }


@pytest.mark.parametrize("kind", ["SALES", "PURCHASE"])
@pytest.mark.parametrize("field", ["qty", "unit_price"])
@pytest.mark.parametrize("value", [1, 0.1, True, "NaN", "Infinity", "-1"])
def test_draft_strict_numbers(kind, field, value):
    order = minimal_order(kind)
    order["lines"][0][field] = value
    with pytest.raises(ValidationError):
        input_body(order, kind)


@pytest.mark.parametrize("kind", ["SALES", "PURCHASE"])
def test_draft_bounds_and_forbidden_origin(kind):
    order = minimal_order(kind)
    valid = input_body(order, kind)
    assert DRAFT_INPUT.validate_json(valid.model_dump_json()) == valid
    for replacement in (
        {"organization_id": str(uuid4())},
        {"user_id": str(uuid4())},
        {"status": "CONFIRMED"},
        {"id": str(uuid4())},
    ):
        with pytest.raises(ValidationError):
            input_body(order | replacement, kind)
        with pytest.raises(ValidationError):
            DRAFT_INPUT.validate_python({"kind": kind, "order": order} | replacement)
    too_many = order | {
        "lines": [order["lines"][0] | {"product_id": str(uuid4())} for _ in range(21)]
    }
    with pytest.raises(ValidationError):
        input_body(too_many, kind)
    order["lines"][0]["qty"] = "0"
    with pytest.raises(ValidationError):
        input_body(order, kind)


async def test_sales_preview_is_read_only_and_creation_is_draft(catalog_client, sale):
    await opening(catalog_client, sale)
    before_balance = await balance(catalog_client, sale)
    body = input_body(sale)
    async with transaction(catalog_client) as (db, ctx):
        before = await counts(db)
        shown = await drafts.preview_draft(db, ctx, body)
        assert await counts(db) == before
        assert shown.total_amount == D("105")
        assert shown.lines[0].qty == 7 and shown.lines[0].price_source["source"] == "manual"
        receipt = await drafts.create_draft(db, ctx, body, shown.confirmation_hash, uuid4().hex)
        assert receipt["status"] == "DRAFT" and receipt["version"] == 1
        after = await counts(db)
        assert after["sales_orders"] == before["sales_orders"] + 1
        for table in (
            "inventory_movements",
            "inventory_reservations",
            "inventory_documents",
            "funds_entries",
            "funds_cash_documents",
        ):
            assert after[table] == before[table]
        audit = (
            await db.execute(
                text(
                    "SELECT actor_type,actor_id,source FROM forge.audit_events "
                    "WHERE resource_id=:id AND action='sales.order.create'"
                ),
                {"id": UUID(receipt["id"])},
            )
        ).one()
        assert tuple(audit) == ("USER", ctx.user_id, "AI")
    assert await balance(catalog_client, sale) == before_balance
    assert (await detail(catalog_client, receipt))["amount"] == "105.0000"


async def test_purchase_preview_and_creation_only_draft(catalog_client, sale):
    supplier = (
        await create(catalog_client, "suppliers", {"code": "AI-S", "name": "供应商"})
    ).json()
    order = {
        "supplier_id": supplier["id"],
        "warehouse_id": sale["warehouse_id"],
        "reason": "采购",
        "lines": [{k: v for k, v in sale["lines"][0].items() if k != "pricing_mode"}],
    }
    body = input_body(order, "PURCHASE")
    shown = await preview(catalog_client, body)
    assert shown.party_name == "供应商" and shown.total_amount == 105
    async with transaction(catalog_client) as (db, ctx):
        before = await counts(db)
        receipt = await drafts.create_draft(db, ctx, body, shown.confirmation_hash, uuid4().hex)
        after = await counts(db)
        assert (
            receipt["status"] == "DRAFT"
            and after["purchase_orders"] == before["purchase_orders"] + 1
        )
        for table in ("inventory_movements", "inventory_documents", "funds_entries"):
            assert after[table] == before[table]


@pytest.mark.parametrize("change", ["price", "price_version", "factor", "name", "inactive"])
async def test_changed_basis_rejects_without_creating(catalog_client, sale, change):
    selected = await price(catalog_client, sale, "standard", "15")
    order = deepcopy(sale)
    order["lines"][0].pop("pricing_mode")
    order["lines"][0].pop("unit_price")
    body = input_body(order)
    shown = await preview(catalog_client, body)
    with create_engine(settings().migration_database_url).begin() as db:
        if change in {"price", "price_version"}:
            db.execute(
                text("UPDATE forge.product_prices SET version=version+1,price=:price WHERE id=:id"),
                {"id": selected["id"], "price": "16" if change == "price" else "15"},
            )
        elif change == "factor":
            db.execute(
                text(
                    "UPDATE forge.product_units SET unit_to_base_factor=2,version=version+1 "
                    "WHERE product_id=:id"
                ),
                {"id": order["lines"][0]["product_id"]},
            )
        else:
            db.execute(
                text(
                    "UPDATE forge.customers SET active=:active,name=:name,version=version+1 "
                    "WHERE id=:id"
                ),
                {"id": order["customer_id"], "active": change != "inactive", "name": "新名称"},
            )
    async with transaction(catalog_client) as (db, ctx):
        before = await counts(db)
        with pytest.raises(Problem) as exc:
            await drafts.create_draft(db, ctx, body, shown.confirmation_hash, uuid4().hex)
        assert exc.value.code in {"AI_DRAFT_CHANGED", "INVALID_REFERENCE"}
        assert await counts(db) == before


@pytest.mark.parametrize(
    "permission",
    [
        "ai.use",
        "ai.draft.create",
        "sales.order.write",
        "product.price.read",
        "customer.read",
    ],
)
async def test_draft_requires_ai_and_business_permissions(catalog_client, sale, permission):
    async with transaction(catalog_client, without=permission) as (db, ctx):
        with pytest.raises(Problem) as exc:
            await drafts.preview_draft(db, ctx, input_body(sale))
        assert exc.value.code == "PERMISSION_DENIED"


async def test_auto_preview_requires_real_price(catalog_client, sale):
    body = deepcopy(sale)
    body["lines"][0].pop("pricing_mode")
    body["lines"][0].pop("unit_price")
    with pytest.raises(Problem) as exc:
        await preview(catalog_client, input_body(body))
    assert exc.value.code == "PRICE_UNSET"


@pytest.mark.parametrize("new_amount", ["16", "15"])
async def test_new_price_between_review_and_command_rolls_back_all(
    catalog_client,
    sale,
    identities,
    monkeypatch,
    new_amount,
):
    """A concurrently inserted higher-priority row evades existing-row shared locks."""
    await price(catalog_client, sale, "standard", "15")
    order = deepcopy(sale)
    order["lines"][0].pop("pricing_mode")
    order["lines"][0].pop("unit_price")
    body = input_body(order)
    shown = await preview(catalog_client, body)
    original = sales_orders.save

    async def racing_save(db, ctx, order, key, id=None):
        with create_engine(settings().migration_database_url).begin() as other:
            other.execute(text("SET LOCAL lock_timeout='2s'"))
            other.execute(
                text(
                    "INSERT INTO forge.product_prices"
                    "(organization_id,product_id,price_type,customer_id,price) "
                    "VALUES(:org,:product,'customer',:customer,:price)"
                ),
                {
                    "org": identities[0]["org"],
                    "product": order.lines[0].product_id,
                    "customer": order.customer_id,
                    "price": new_amount,
                },
            )
        return await original(db, ctx, order, key, id=id)

    monkeypatch.setattr(sales_orders, "save", racing_save)
    async with transaction(catalog_client) as (db, ctx):
        before = await counts(db)
        with pytest.raises(Problem) as exc:
            await drafts.create_draft(db, ctx, body, shown.confirmation_hash, uuid4().hex)
        assert exc.value.code == "AI_DRAFT_CHANGED"
        assert await counts(db) == before


async def test_review_hash_binds_input_and_owner(catalog_client, sale):
    body = input_body(sale)
    shown = await preview(catalog_client, body)
    changed = input_body(sale | {"reason": "另一用途"})
    async with transaction(catalog_client) as (db, ctx):
        with pytest.raises(Problem) as exc:
            await drafts.create_draft(db, ctx, changed, shown.confirmation_hash, uuid4().hex)
        assert exc.value.code == "AI_DRAFT_CHANGED"
        other = await drafts.preview_draft(db, replace(ctx, user_id=uuid4()), body)
        assert other.confirmation_hash != shown.confirmation_hash


async def test_new_posted_history_between_preview_and_save_is_not_auto_approved(
    catalog_client,
    sale,
    monkeypatch,
):
    await opening(catalog_client, sale, "30")
    previous = await so(catalog_client, sale)
    assert (await action(catalog_client, previous)).status_code == 200
    shipping, _ = await shipment(catalog_client, previous)
    await price(catalog_client, sale, "standard", "15")
    order = deepcopy(sale)
    order["lines"][0].pop("pricing_mode")
    order["lines"][0].pop("unit_price")
    body = input_body(order)
    shown = await preview(catalog_client, body)
    original = sales_orders.save

    async def racing_save(db, ctx, order, key, id=None):
        response = await post_shipment(catalog_client, shipping)
        assert response.status_code == 200, response.text
        return await original(db, ctx, order, key, id=id)

    monkeypatch.setattr(sales_orders, "save", racing_save)
    async with transaction(catalog_client) as (db, ctx):
        with pytest.raises(Problem) as exc:
            await drafts.create_draft(db, ctx, body, shown.confirmation_hash, uuid4().hex)
        assert exc.value.code == "AI_DRAFT_CHANGED"
        # The independent, legitimate shipment survives; the attempted AI order does not.
        assert (await db.execute(text("SELECT count(*) FROM forge.sales_orders"))).scalar_one() == 1
        assert (
            await db.execute(text("SELECT count(*) FROM forge.audit_events WHERE source='AI'"))
        ).scalar_one() == 0
