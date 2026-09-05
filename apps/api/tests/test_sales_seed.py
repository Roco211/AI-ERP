"""Optional sales samples must be atomic, tenant-scoped and permanently non-destructive."""

import asyncio
from dataclasses import replace
from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_sales_orders import action
from test_sales_returns import reverse

from forge_erp.core.config import settings
from forge_erp.core.db import sessions, set_tenant
from forge_erp.core.errors import Problem
from forge_erp.modules.inventory.application.engine import InventoryEngine
from forge_erp.modules.inventory.application.maintenance import operator_context
from forge_erp.modules.sales.application.dev_seed import (
    REQUIRED_PERMISSIONS,
    SEED_MARKER,
    seed_sales,
)

TABLES = (
    "categories",
    "units",
    "warehouses",
    "customers",
    "products",
    "product_units",
    "product_prices",
    "sales_orders",
    "sales_order_lines",
    "sales_documents",
    "sales_document_lines",
    "inventory_documents",
    "inventory_document_lines",
    "inventory_movements",
    "inventory_balances",
    "inventory_reservations",
    "audit_events",
    "outbox_events",
    "idempotency_keys",
)


@pytest.fixture
def seed_context(catalog_client, identities):
    return operator_context(identities[0]["code"], "same@example.test")


async def counts(ctx):
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        return {
            table: (
                await db.execute(
                    text(f"SELECT count(*) FROM forge.{table} WHERE organization_id=:org"),
                    {"org": ctx.organization_id},
                )
            ).scalar_one()
            for table in TABLES
        }


@pytest.mark.parametrize("environment", ["production", "staging", "test"])
async def test_sales_seed_refuses_non_development_before_writing(
    catalog_client, seed_context, monkeypatch, environment
):
    before = await counts(seed_context)
    monkeypatch.setattr(settings(), "app_env", environment)
    with pytest.raises(RuntimeError, match="development-only"):
        await seed_sales(seed_context)
    assert await counts(seed_context) == before


@pytest.mark.parametrize("permission", ["sales.return", "product.cost.read", "catalog.write"])
async def test_sales_seed_uses_operator_permissions_and_denies_partial_setup(
    catalog_client, seed_context, identities, permission
):
    before = await counts(seed_context)
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                "AND permission_code=:permission"
            ),
            identities[0] | {"permission": permission},
        )
    restricted = operator_context(identities[0]["code"], "same@example.test")
    assert permission not in restricted.permissions
    with pytest.raises(Problem) as denied:
        await seed_sales(restricted)
    assert denied.value.status == 403
    assert await counts(seed_context) == before


async def test_sales_seed_real_commands_quantities_costs_audit_and_tenant_isolation(
    catalog_client, seed_context, identities
):
    result = await seed_sales(seed_context)
    assert result["status"] == "created"
    order = result["order"]
    assert order["status"] == "CONFIRMED" and order["fulfillment_status"] == "PARTIAL"
    line = order["lines"][0]
    expected = {
        "qty": 100,
        "shipped_base_qty": 60,
        "returned_base_qty": 10,
        "remaining_qty": 40,
        "reserved_base_qty": 40,
        "on_hand_qty": 150,
        "warehouse_reserved_qty": 40,
        "available_qty": 110,
        "unit_price": 15,
        "amount": 1500,
    }
    for key, value in expected.items():
        assert D(line[key]) == value
    assert line["price_source"]["source"] == "standard"
    assert [D(order[key]) for key in ("net_sales_amount", "net_cost", "gross_margin")] == [
        750,
        550,
        200,
    ]
    assert {row["kind"] for row in result["documents"]} == {"SHIPMENT", "RETURN"}
    async with sessions.begin() as db:
        await set_tenant(db, seed_context.organization_id)
        role = (await db.execute(text("SELECT current_user"))).scalar_one()
        assert role == "forge_app"
        engine = InventoryEngine(db, seed_context, "inventory.reconcile")
        await engine.lock([(order["warehouse_id"], line["product_id"])])
        assert all(not row["differences"] for row in await engine.reconcile())
        facts = (
            await db.execute(
                text(
                    "SELECT kind,base_qty,value_delta FROM forge.inventory_movements "
                    "ORDER BY sequence"
                )
            )
        ).all()
        assert facts == [
            ("RECEIVE", 200, 2200),
            ("RESERVE", 0, 0),
            ("ISSUE", -60, -660),
            ("RECEIVE", 10, 110),
        ]
        audit = (
            (
                await db.execute(
                    text(
                        "SELECT actor_type,actor_id,source,action FROM forge.audit_events "
                        "WHERE request_id=:marker"
                    ),
                    {"marker": SEED_MARKER},
                )
            )
            .mappings()
            .all()
        )
        assert len(audit) == 19
        assert sum(row["action"] == "inventory.movement.recorded" for row in audit) == 4
        assert all(
            row["actor_type"] == "USER" and row["actor_id"] == seed_context.user_id for row in audit
        )
        assert all(row["source"] == "SYSTEM" for row in audit)
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.outbox_events WHERE request_id=:marker"),
                {"marker": SEED_MARKER},
            )
        ).scalar_one() == len(audit)
    other = operator_context(identities[1]["code"], "same@example.test")
    assert all(value == 0 for value in (await counts(other)).values())


async def test_sales_seed_repeated_and_expired_keys_never_create_again(
    catalog_client, seed_context
):
    first = await seed_sales(seed_context)
    before = await counts(seed_context)
    for _ in range(2):
        again = await seed_sales(seed_context)
        assert again["status"] == "preserved" and again["order_id"] == first["order_id"]
        assert await counts(seed_context) == before
    async with sessions.begin() as db:
        await set_tenant(db, seed_context.organization_id)
        await db.execute(
            text("UPDATE forge.idempotency_keys SET expires_at=now()-interval '1 day'")
        )
    again = await seed_sales(seed_context)
    assert again["status"] == "preserved" and again["order_id"] == first["order_id"]
    assert await counts(seed_context) == before
    async with sessions.begin() as db:
        await set_tenant(db, seed_context.organization_id)
        await db.execute(text("DELETE FROM forge.idempotency_keys"))
    expired = await counts(seed_context)
    again = await seed_sales(seed_context)
    assert again["order_id"] == first["order_id"]
    assert await counts(seed_context) == expired


async def test_sales_seed_concurrent_runs_share_one_complete_demo(catalog_client, seed_context):
    results = await asyncio.gather(seed_sales(seed_context), seed_sales(seed_context))
    assert sorted(row["status"] for row in results) == ["created", "preserved"]
    assert results[0]["order_id"] == results[1]["order_id"]
    quantities = await counts(seed_context)
    assert quantities["sales_orders"] == 1 and quantities["inventory_movements"] == 4


async def test_sales_seed_preserves_renamed_records_closed_order_and_reversed_return(
    catalog_client, seed_context
):
    c = catalog_client
    first = await seed_sales(seed_context)
    order = first["order"]
    customer = (await c.get("/api/v1/customers/" + str(order["customer_id"]))).json()
    changed = await c.put(
        "/api/v1/customers/" + customer["id"],
        headers={"Idempotency-Key": uuid4().hex},
        json={
            "code": "SALE-DEMO-RENAMED",
            "name": "用户保留客户名称",
            "expected_version": customer["version"],
        },
    )
    assert changed.status_code == 200, changed.text
    returned = next(row for row in first["documents"] if row["kind"] == "RETURN")
    undone = await reverse(c, returned | {"id": str(returned["id"])})
    assert undone.status_code == 200, undone.text
    closed = await action(c, order | {"id": str(order["id"])}, "close", "用户结束演示订单")
    assert closed.status_code == 200, closed.text
    before = await counts(seed_context)
    again = await seed_sales(seed_context)
    assert again["status"] == "preserved" and again["order_id"] == first["order_id"]
    assert again["order"]["status"] == "CLOSED"
    assert D(again["order"]["lines"][0]["executable_qty"]) == 0
    assert (
        next(row for row in again["documents"] if row["kind"] == "RETURN")["status"] == "REVERSED"
    )
    assert "未写入" in again["message"]
    assert await counts(seed_context) == before
    current = await c.get("/api/v1/customers/" + customer["id"])
    assert current.json()["code"] == "SALE-DEMO-RENAMED"


async def test_sales_seed_existing_code_is_preserved_without_adopting_it(
    catalog_client, seed_context
):
    assert (
        await create(catalog_client, "warehouses", {"code": "SALE-DEMO", "name": "已有仓库"})
    ).status_code == 201
    before = await counts(seed_context)
    result = await seed_sales(seed_context)
    assert result["status"] == "preserved" and "order_id" not in result
    assert result["existing_codes"] == ["warehouses:SALE-DEMO"]
    assert await counts(seed_context) == before


async def test_sales_seed_audit_marker_alone_is_not_completion_evidence(
    catalog_client, seed_context
):
    async with sessions.begin() as db:
        await set_tenant(db, seed_context.organization_id)
        await db.execute(
            text(
                "INSERT INTO forge.audit_events(organization_id,actor_type,actor_id,action,"
                "resource_type,resource_id,request_id,source,after) VALUES "
                "(:org,'USER',:actor,'sales.order.create','sales_order',:id,:marker,'SYSTEM','{}')"
            ),
            {
                "org": seed_context.organization_id,
                "actor": seed_context.user_id,
                "id": uuid4(),
                "marker": SEED_MARKER,
            },
        )
    before = await counts(seed_context)
    result = await seed_sales(seed_context)
    assert result["status"] == "preserved" and "order_id" not in result
    assert "无法完整核验" in result["message"]
    assert await counts(seed_context) == before


@pytest.mark.parametrize(
    "marker", ["INSERT INTO forge.inventory_movements", "INSERT INTO forge.outbox_events", "COMMIT"]
)
async def test_sales_seed_failure_rolls_back_every_record_and_allows_clean_retry(
    catalog_client, seed_context, monkeypatch, marker
):
    before = await counts(seed_context)
    execute = AsyncSession.execute
    injected = False

    async def failed(self, statement, *args, **kwargs):
        nonlocal injected
        result = await execute(self, statement, *args, **kwargs)
        if marker in str(statement) and not injected:
            injected = True
            raise RuntimeError("injected sales seed failure")
        return result

    def failed_commit(session):
        nonlocal injected
        injected = True
        raise RuntimeError("injected sales seed failure")

    if marker == "COMMIT":
        event.listen(Session, "before_commit", failed_commit)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "execute", failed)
            with pytest.raises(RuntimeError, match="injected"):
                await seed_sales(seed_context)
    finally:
        if marker == "COMMIT":
            event.remove(Session, "before_commit", failed_commit)
    assert injected
    assert await counts(seed_context) == before
    assert (await seed_sales(seed_context))["status"] == "created"


async def test_sales_seed_rejects_unprivileged_context_even_after_prior_success(
    catalog_client, seed_context
):
    await seed_sales(seed_context)
    before = await counts(seed_context)
    with pytest.raises(Problem) as denied:
        await seed_sales(replace(seed_context, permissions=REQUIRED_PERMISSIONS - {"sales.read"}))
    assert denied.value.status == 403
    assert await counts(seed_context) == before
