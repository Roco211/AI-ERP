import asyncio
from decimal import Decimal as D
from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_catalog import catalog_client as catalog_client
from test_catalog import create, product_fixture

from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions, set_tenant
from forge_erp.core.errors import Problem
from forge_erp.modules.inventory.application.engine import InventoryEngine
from forge_erp.modules.inventory.domain.values import Balance, InventoryError

PERMISSIONS = frozenset(
    {"inventory.adjust", "inventory.reverse", "inventory.reconcile", "product.cost.read"}
)


@pytest.fixture
async def stock(catalog_client, identities):
    p, _, _, unit = await product_fixture(catalog_client)
    wh = (await create(catalog_client, "warehouses", {"code": "MAIN", "name": "主仓"})).json()
    other = (await create(catalog_client, "warehouses", {"code": "OTHER", "name": "分仓"})).json()
    identity = identities[0]
    ctx = RuntimeContext(identity["org"], identity["user"], PERMISSIONS, "inventory-test")
    key = (UUID(wh["id"]), UUID(p["id"]))
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        doc = (
            await db.execute(
                text(
                    "INSERT INTO forge.inventory_documents "
                    "(organization_id,number,type,reason,warehouse_id,"
                    "target_warehouse_id,created_by) VALUES "
                    "(:org,:number,'TRANSFER','test',:wh,:target,:user) RETURNING id"
                ),
                {
                    "org": ctx.organization_id,
                    "number": uuid4().hex,
                    "wh": key[0],
                    "target": other["id"],
                    "user": ctx.user_id,
                },
            )
        ).scalar_one()
        line = (
            await db.execute(
                text(
                    "INSERT INTO forge.inventory_document_lines "
                    "(organization_id,document_id,line_no,product_id,"
                    "unit_id,product_label,unit_label,"
                    "qty,unit_to_base_factor,base_qty,conversion_version,direction) VALUES "
                    "(:org,:doc,1,:product,:unit,'Bolt','个',1,1,1,1,'IN') RETURNING id"
                ),
                {"org": ctx.organization_id, "doc": doc, "product": key[1], "unit": unit["id"]},
            )
        ).scalar_one()
    return ctx, key, line, doc, UUID(other["id"])


async def run_change(stock, kind, qty, cost=None, reservation=None):
    ctx, key, line, _, _ = stock
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        e = InventoryEngine(db, ctx)
        await e.lock([key])
        m = await e.change(
            key,
            line,
            uuid4(),
            kind,
            D(qty),
            cost=D(cost) if cost else None,
            reservation_id=reservation,
        )
        return m, e.state(key)


def test_cost_and_precision():
    b = Balance().receive(D(100), D(10)).receive(D(100), D(12))
    assert b == Balance(D(200), D(0), D(2200), D(11))
    assert b.issue(D(50)) == Balance(D(150), D(0), D(1650), D(11))
    assert b.issue(D(200)).value == 0
    assert b.issue(D(200)).receive(D(2), D(7)).cost == 7
    tiny = Balance().receive(D(3), D(".000067")).issue(D(1)).issue(D(1))
    with pytest.raises(InventoryError, match="库存金额"):
        tiny.issue(D(".9"))
    assert tiny.issue(D(1)).value == 0
    for value in (0.1, D("NaN"), D("Infinity"), D("1e14"), D(".0000001")):
        with pytest.raises(InventoryError):
            Balance().receive(value, D(1))


@given(st.lists(st.tuples(st.integers(1, 1000), st.integers(0, 1000)), min_size=1, max_size=30))
def test_generated_cost_and_reservation_sequences(operations):
    b = Balance()
    quantity = D(0)
    value = D(0)
    for q, c in operations:
        b = b.receive(D(q), D(c))
        quantity += q
        value += q * c
        assert b.qty == quantity and b.value == value and b.reserved == 0
        before = b
        b = b.reserve(D(q)).reserve(-D(q))
        assert b == before
    if b.qty > 1:
        previous = b
        b = b.issue(D(1))
        assert b.qty == previous.qty - 1 and b.cost == previous.cost and b.reserved == 0
    b = b.issue(b.qty)
    assert b.qty == b.value == b.reserved == 0


async def test_live_ledger_cost_reservation_and_reconciliation(stock):
    await run_change(stock, "RECEIVE", "100", "10")
    await run_change(stock, "RECEIVE", "100", "12")
    m, b = await run_change(stock, "RESERVE", "60")
    assert (b.qty, b.reserved, b.available) == (200, 60, 140)
    rid = m["reservation_id"]
    _, b = await run_change(stock, "ISSUE", "20", reservation=rid)
    assert (b.qty, b.reserved, b.available, b.value) == (180, 40, 140, 1980)
    _, b = await run_change(stock, "RELEASE", "10", reservation=rid)
    assert (b.reserved, b.available) == (30, 150)
    with pytest.raises(Problem, match="占用不足"):
        await run_change(stock, "RELEASE", "31", reservation=rid)
    ctx, key, *_ = stock
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        e = InventoryEngine(db, ctx)
        await e.lock([key])
        report = await e.reconcile()
        assert report[0]["differences"] == {}
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.audit_events WHERE "
                    "action='inventory.movement.recorded'"
                )
            )
        ).scalar_one() == 5


async def test_real_concurrent_issue_and_first_row(stock):
    results = await asyncio.gather(
        run_change(stock, "RECEIVE", "5", "10"), run_change(stock, "RECEIVE", "5", "10")
    )
    assert max(r[1].qty for r in results) == 10
    outcomes = await asyncio.gather(
        run_change(stock, "ISSUE", "7"), run_change(stock, "ISSUE", "7"), return_exceptions=True
    )
    assert sum(isinstance(x, Problem) and x.code == "INSUFFICIENT_STOCK" for x in outcomes) == 1
    ctx, key, *_ = stock
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        e = InventoryEngine(db, ctx)
        await e.lock([key])
        assert e.state(key).qty == 3 and not (await e.reconcile())[0]["differences"]


async def test_inventory_rls_and_immutability(stock, identities):
    m, _ = await run_change(stock, "RECEIVE", "10", "5")
    for sql in (
        "UPDATE forge.inventory_movements SET base_qty=1",
        "DELETE FROM forge.inventory_movements",
        "TRUNCATE forge.inventory_movements",
    ):
        with pytest.raises(DBAPIError):
            async with sessions.begin() as db:
                await set_tenant(db, stock[0].organization_id)
                await db.execute(text(sql))
    async with sessions.begin() as db:
        await set_tenant(db, identities[1]["org"])
        for table in (
            "inventory_documents",
            "inventory_document_lines",
            "inventory_balances",
            "inventory_movements",
            "inventory_reservations",
            "inventory_reversals",
        ):
            assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar_one() == 0
        assert (
            await db.execute(
                text("UPDATE forge.inventory_balances SET on_hand_qty=0 WHERE id=:id"),
                {"id": m["id"]},
            )
        ).rowcount == 0
    with pytest.raises(DBAPIError):
        async with sessions.begin() as db:
            await set_tenant(db, identities[1]["org"])
            await db.execute(
                text(
                    "INSERT INTO forge.inventory_balances "
                    "(organization_id,warehouse_id,product_id) VALUES (:org,:wh,:product)"
                ),
                {"org": identities[1]["org"], "wh": stock[1][0], "product": stock[1][1]},
            )


async def test_engine_rollback_on_audit_failure(stock, monkeypatch):
    import forge_erp.modules.inventory.application.engine as module

    async def fail(*args, **kwargs):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(module, "record_mutation", fail)
    with pytest.raises(RuntimeError):
        await run_change(stock, "RECEIVE", "10", "5")
    async with sessions.begin() as db:
        await set_tenant(db, stock[0].organization_id)
        for table in ("inventory_balances", "inventory_movements"):
            assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar_one() == 0
