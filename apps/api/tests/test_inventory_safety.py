import asyncio
from dataclasses import replace
from decimal import Decimal as D
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_inventory_documents import balance, command, draft
from test_inventory_engine import run_change
from test_inventory_engine import stock as stock

from forge_erp.core.config import settings
from forge_erp.core.db import sessions, set_tenant
from forge_erp.core.errors import Problem
from forge_erp.modules.inventory.application import documents
from forge_erp.modules.inventory.application.engine import InventoryEngine
from forge_erp.modules.inventory.application.maintenance import reconcile_scope


@pytest.mark.parametrize(
    "after",
    [
        "INSERT INTO forge.inventory_movements",
        "UPDATE forge.inventory_balances",
        "UPDATE forge.inventory_documents SET status='POSTED'",
        "INSERT INTO forge.audit_events",
        "INSERT INTO forge.outbox_events",
        "INSERT INTO forge.idempotency_keys",
    ],
)
async def test_post_faults_rollback_and_safe_retry(catalog_client, stock, monkeypatch, after):
    c = catalog_client
    doc, _ = await draft(c, stock)
    execute = AsyncSession.execute

    async def fail(self, statement, *args, **kwargs):
        result = await execute(self, statement, *args, **kwargs)
        if after in str(statement):
            raise RuntimeError("injected transaction failure")
        return result

    ctx = replace(stock[0], permissions=stock[0].permissions | {"inventory.opening"})
    key = uuid4().hex
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", fail)
        with pytest.raises(RuntimeError):
            async with sessions.begin() as db:
                await set_tenant(db, ctx.organization_id)
                await documents.transition(db, ctx, UUID(doc["id"]), 1, key, "post")
    assert D((await balance(c, stock))["on_hand_qty"]) == 0
    assert (await c.get("/api/v1/inventory/documents/" + doc["id"])).json()["status"] == "DRAFT"
    assert (await c.get("/api/v1/inventory/movements")).json()["items"] == []
    assert (await command(c, doc, key=key)).status_code == 200


async def test_rebuild_repairs_projection_and_dry_run_does_not_write(stock):
    await run_change(stock, "RECEIVE", "100", "10")
    m, _ = await run_change(stock, "RESERVE", "60")
    ctx, key, *_ = stock
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "UPDATE forge.inventory_balances SET on_hand_qty=105,"
                "inventory_value=1050 WHERE organization_id=:org"
            ),
            {"org": ctx.organization_id},
        )
        db.execute(
            text(
                "UPDATE forge.inventory_reservations SET reserved_qty=59,"
                "remaining_qty=59 WHERE id=:id"
            ),
            {"id": m["reservation_id"]},
        )
    report = await reconcile_scope(ctx, key[0], [key[1]])
    assert report[0]["differences"] and not report[0]["repaired"]
    report = await reconcile_scope(ctx, key[0], [key[1]], True)
    assert report[0]["repaired"]
    assert (await reconcile_scope(ctx, key[0], [key[1]]))[0]["differences"] == {}
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        assert (
            await db.execute(text("SELECT count(*) FROM forge.inventory_movements"))
        ).scalar_one() == 2
    with pytest.raises(Problem):
        await reconcile_scope(replace(ctx, permissions=frozenset()), key[0], [key[1]], True)


async def test_no_context_and_cross_tenant_api(catalog_client, stock, identities):
    c = catalog_client
    doc, _ = await draft(c, stock)
    await command(c, doc)
    async with sessions.begin() as db:
        for table in ("inventory_documents", "inventory_movements", "inventory_balances"):
            assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar_one() == 0
    other = identities[1]
    await create(c, "auth/logout", {})
    login = await create(
        c,
        "auth/login",
        {
            "organization_code": other["code"],
            "email": "same@example.test",
            "password": "test-only-password-8472",
        },
    )
    assert login.status_code == 200
    assert (await c.get("/api/v1/inventory/documents/" + doc["id"])).status_code == 404
    assert (await command(c, doc)).status_code == 404
    assert (await c.get("/api/v1/inventory/balances")).json()["items"] == []


async def test_unit_version_and_snapshot_validation(catalog_client, stock):
    c = catalog_client
    doc, body = await draft(c, stock)
    conversions = (
        await c.get("/api/v1/product-units", params={"product_id": str(stock[1][1])})
    ).json()["items"]
    conversion = conversions[0]
    updated = await c.put(
        "/api/v1/product-units/" + conversion["id"],
        json={
            "expected_version": conversion["version"],
            "product_id": conversion["product_id"],
            "unit_id": conversion["unit_id"],
            "unit_to_base_factor": "1",
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert updated.status_code == 200
    assert (await command(c, doc)).json()["code"] == "UNIT_CONVERSION_CHANGED"
    saved = await c.put(
        "/api/v1/inventory/documents/" + doc["id"] + "/draft",
        json=body | {"expected_version": 1},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert saved.status_code == 200, saved.text
    assert (await command(c, saved.json())).status_code == 200
    for patch in (
        {"organization_id": str(stock[0].organization_id)},
        {"lines": []},
        {"lines": body["lines"] * 201},
    ):
        result = await create(c, "inventory/openings", body | patch)
        assert result.status_code == 422, result.text


async def test_low_stock_scope_and_outbox_recovery(catalog_client, stock):
    c = catalog_client
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text("UPDATE forge.products SET min_stock_qty=80 WHERE id=:id"), {"id": stock[1][1]}
        )
    assert (await c.get("/api/v1/inventory/low-stock")).json()["total"] == 1
    doc, _ = await draft(c, stock)
    await command(c, doc)
    assert (await c.get("/api/v1/inventory/low-stock")).json()["total"] == 0
    move, _ = await draft(c, stock, "transfers", qty="70")
    await command(c, move)
    assert (await c.get("/api/v1/inventory/low-stock")).json()["total"] == 0
    from forge_erp.workers.outbox import drain_outbox

    async with sessions.begin() as db:
        await set_tenant(db, stock[0].organization_id)
        count = (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.outbox_events WHERE event_type LIKE "
                    "'inventory.%' AND processed_at IS NULL"
                )
            )
        ).scalar_one()
    assert count > 0
    await drain_outbox(stock[0].organization_id)
    async with sessions.begin() as db:
        await set_tenant(db, stock[0].organization_id)
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.outbox_events WHERE event_type LIKE "
                    "'inventory.%' AND processed_at IS NULL"
                )
            )
        ).scalar_one() == 0
    assert await drain_outbox(stock[0].organization_id) == 0


async def test_opposing_transfer_and_independent_stock_locks(catalog_client, stock):
    c = catalog_client
    ctx, key, line, _, target = stock
    await run_change(stock, "RECEIVE", "100", "10")
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        e = InventoryEngine(db, ctx)
        await e.lock([(target, key[1])])
        await e.change((target, key[1]), line, uuid4(), "RECEIVE", D(100), cost=D(20))

    async def transfer(a, b):
        async with sessions.begin() as db:
            await set_tenant(db, ctx.organization_id)
            e = InventoryEngine(db, ctx)
            await e.lock([(a, key[1]), (b, key[1])])
            op = uuid4()
            m = await e.change((a, key[1]), line, op, "TRANSFER_OUT", D(10))
            await e.change((b, key[1]), line, op, "TRANSFER_IN", D(10), value=-m["value_delta"])

    await asyncio.wait_for(asyncio.gather(transfer(key[0], target), transfer(target, key[0])), 5)
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        e = InventoryEngine(db, ctx)
        await e.lock([key])

        async def independent():
            async with sessions.begin() as other:
                await set_tenant(other, ctx.organization_id)
                second = InventoryEngine(other, ctx)
                await second.lock([(target, key[1])])
                return second.state((target, key[1])).qty

        assert await asyncio.wait_for(independent(), 2) == 100
    rows = (await c.get("/api/v1/inventory/balances")).json()["items"]
    assert sum(D(x["inventory_value"]) for x in rows) == 3000


async def test_deactivation_waits_for_incoming_stock(catalog_client, stock):
    from forge_erp.modules.catalog.application.service import write_command

    ctx, key, line, _, _ = stock
    writer = replace(ctx, permissions=ctx.permissions | {"warehouse.write"})
    entered = asyncio.Event()

    async def deactivate():
        async with sessions.begin() as db:
            await set_tenant(db, ctx.organization_id)
            entered.set()
            return await write_command(
                db, writer, "warehouses", {}, uuid4().hex, key[0], 1, active=False
            )

    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        e = InventoryEngine(db, ctx)
        await e.lock([key])
        attempt = asyncio.create_task(deactivate())
        await entered.wait()
        await e.change(key, line, uuid4(), "RECEIVE", D(10), cost=D(5))
    with pytest.raises(Problem) as rejected:
        await attempt
    assert rejected.value.code == "STOCK_IN_USE"


async def test_old_idempotency_cannot_repeat_post_and_cost_permission_revoked(
    catalog_client, stock
):
    c = catalog_client
    doc, _ = await draft(c, stock)
    key = uuid4().hex
    assert (await command(c, doc, key=key)).status_code == 200
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "UPDATE forge.idempotency_keys SET expires_at=now()-interval '1 second' "
                "WHERE organization_id=:org"
            ),
            {"org": stock[0].organization_id},
        )
    assert (await command(c, doc, key=key)).status_code == 409
    assert D((await balance(c, stock))["on_hand_qty"]) == 100
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                "AND permission_code='product.cost.read'"
            ),
            {"org": stock[0].organization_id},
        )
    assert (await command(c, doc, key=key)).status_code == 403


async def test_exact_box_snapshot_and_subprecision_rejected(catalog_client, stock):
    c = catalog_client
    box = (await create(c, "units", {"code": "BOX", "name": "箱"})).json()
    conversion = (
        await create(
            c,
            "product-units",
            {"product_id": str(stock[1][1]), "unit_id": box["id"], "unit_to_base_factor": "1000"},
        )
    ).json()
    body = {
        "warehouse_id": str(stock[1][0]),
        "reason": "整箱期初",
        "lines": [
            {
                "product_id": str(stock[1][1]),
                "unit_id": box["id"],
                "qty": "2",
                "input_unit_cost": "1",
            }
        ],
    }
    doc = (await create(c, "inventory/openings", body)).json()
    assert (await command(c, doc)).status_code == 200
    assert D((await balance(c, stock))["on_hand_qty"]) == 2000
    change = await c.put(
        "/api/v1/product-units/" + conversion["id"],
        headers={"Idempotency-Key": uuid4().hex},
        json={
            "product_id": str(stock[1][1]),
            "unit_id": box["id"],
            "unit_to_base_factor": "0.000001",
            "expected_version": 1,
        },
    )
    assert change.status_code == 200
    historical = (await c.get("/api/v1/inventory/documents/" + doc["id"])).json()["lines"][0]
    assert D(historical["base_qty"]) == 2000 and D(historical["unit_to_base_factor"]) == 1000
    bad = await create(
        c, "inventory/adjustments", body | {"lines": [body["lines"][0] | {"qty": "0.000001"}]}
    )
    assert bad.status_code == 422 and bad.json()["code"] == "INVALID_UNIT_QUANTITY"


async def test_pending_transaction_excluded_from_existing_cursor(catalog_client, stock):
    c = catalog_client
    for _ in range(3):
        await run_change(stock, "RECEIVE", "1", "1")
    ctx, key, line, _, _ = stock
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        e = InventoryEngine(db, ctx)
        await e.lock([key])
        pending = await e.change(key, line, uuid4(), "RECEIVE", D(1), cost=D(1))
        first = (await c.get("/api/v1/inventory/movements?page_size=1")).json()
    ids = [x["id"] for x in first["items"]]
    cursor = first.get("next_cursor")
    while cursor:
        page = (
            await c.get("/api/v1/inventory/movements", params={"page_size": 1, "cursor": cursor})
        ).json()
        ids.extend(x["id"] for x in page["items"])
        cursor = page.get("next_cursor")
    assert len(ids) == 3 and str(pending["id"]) not in ids
    assert len((await c.get("/api/v1/inventory/movements")).json()["items"]) == 4


def test_only_inventory_engine_writes_balance_and_domain_has_no_http_dependency():
    import ast
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "forge_erp"
    for file in root.rglob("*.py"):
        tree = ast.parse(file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if re.search(
                    r"\b(?:UPDATE|INSERT INTO|DELETE FROM)\s+forge\.inventory_balances",
                    node.value,
                    re.I,
                ):
                    assert file == root / "modules/inventory/application/engine.py"
            if "inventory/domain" in str(file) and isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("fastapi", "starlette"))
