from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from test_catalog import catalog_client as catalog_client
from test_sales_orders import action, balance, detail, opening, so
from test_sales_orders import sale as sale

from forge_erp.core.db import sessions, set_tenant
from forge_erp.workers.outbox import drain_outbox


@pytest.mark.parametrize(
    "marker",
    [
        "INSERT INTO forge.inventory_movements",
        "UPDATE forge.inventory_balances",
        "UPDATE forge.inventory_documents",
        "UPDATE forge.sales_orders",
        "INSERT INTO forge.audit_events",
        "INSERT INTO forge.outbox_events",
        "INSERT INTO forge.idempotency_keys",
        "COMMIT",
    ],
)
async def test_sales_confirm_failure_rollback_and_original_key_retry(
    catalog_client, sale, identities, monkeypatch, marker
):
    c = catalog_client
    await opening(c, sale)
    row = await so(c, sale)
    original = AsyncSession.execute
    key = uuid4().hex
    injected = False

    async def broken(self, statement, *args, **kwargs):
        nonlocal injected
        result = await original(self, statement, *args, **kwargs)
        if marker in str(statement) and not injected:
            injected = True
            raise RuntimeError("injected sales failure")
        return result

    def fail_commit(session):
        nonlocal injected
        injected = True
        raise RuntimeError("injected commit failure")

    if marker == "COMMIT":
        event.listen(Session, "before_commit", fail_commit)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "execute", broken)
            response = await action(c, row, key=key)
    finally:
        if marker == "COMMIT":
            event.remove(Session, "before_commit", fail_commit)
    assert injected and response.status_code == 500
    assert "injected" not in response.text
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
                text("SELECT count(*) FROM forge.audit_events WHERE action='sales.order.confirm'")
            )
        ).scalar() == 0
    assert (await action(c, row, key=key)).status_code == 200
    assert D((await detail(c, row))["lines"][0]["reserved_base_qty"]) == 7


async def test_sales_audit_outbox_replay_does_not_repeat_reservation(
    catalog_client, sale, identities
):
    c = catalog_client
    await opening(c, sale)
    row = await so(c, sale)
    assert (await action(c, row)).status_code == 200
    before = await balance(c, sale)
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        rows = (
            (
                await db.execute(
                    text("SELECT payload FROM forge.outbox_events WHERE event_type LIKE 'sales.%'")
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 2
        for r in rows:
            assert set(r["payload"]) <= {"resource_id", "resource_type", "version"}
    await drain_outbox(identities[0]["org"])
    await drain_outbox(identities[0]["org"])
    assert await balance(c, sale) == before
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.outbox_events WHERE event_type LIKE "
                    "'sales.%' AND processed_at IS NULL"
                )
            )
        ).scalar() == 0


async def test_sales_order_rls_write_and_foreign_tenant_fk(catalog_client, sale, identities):
    import json

    from sqlalchemy.exc import DBAPIError

    c = catalog_client
    row = await so(c, sale)
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        snapshot = (
            await db.execute(
                text("SELECT to_jsonb(o) FROM forge.sales_orders o WHERE id=:id"), {"id": row["id"]}
            )
        ).scalar_one()
    for target_org, new_org in [
        (identities[1]["org"], identities[0]["org"]),
        (None, identities[0]["org"]),
        (identities[1]["org"], identities[1]["org"]),
    ]:
        data = snapshot | {
            "id": str(uuid4()),
            "number": "FORGED-" + uuid4().hex,
            "organization_id": str(new_org),
        }
        with pytest.raises(DBAPIError) as exc:
            async with sessions.begin() as db:
                if target_org:
                    await set_tenant(db, target_org)
                await db.execute(
                    text(
                        "INSERT INTO forge.sales_orders SELECT * FROM "
                        "jsonb_populate_record(NULL::forge.sales_orders,CAST(:data AS "
                        "jsonb))"
                    ),
                    {"data": json.dumps(data)},
                )
        assert exc.value.orig.sqlstate in {"42501", "23503"}


async def test_sales_reservation_engine_capability_and_sql_append_guard(
    catalog_client, sale, identities
):
    from uuid import UUID

    from sqlalchemy.exc import DBAPIError

    from forge_erp.core.context import RuntimeContext
    from forge_erp.core.errors import Problem
    from forge_erp.modules.inventory.application.engine import InventoryEngine

    c = catalog_client
    await opening(c, sale)
    row = await so(c, sale)
    assert (await action(c, row)).status_code == 200
    ident = identities[0]
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        movement = dict(
            (await db.execute(text("SELECT * FROM forge.inventory_movements WHERE kind='RESERVE'")))
            .mappings()
            .one()
        )
        ctx = RuntimeContext(
            ident["org"],
            ident["user"],
            frozenset({"inventory.adjust", "inventory.reverse"}),
            "engine-capability",
        )
        engine = InventoryEngine(db, ctx)
        key = (UUID(sale["warehouse_id"]), UUID(sale["lines"][0]["product_id"]))
        await engine.lock([key])
        with pytest.raises(Problem, match="销售库存"):
            await engine.change(
                key,
                movement["line_id"],
                uuid4(),
                "RELEASE",
                D("1"),
                reservation_id=movement["reservation_id"],
            )
        with pytest.raises(Problem, match="销售库存"):
            await engine.reverse(movement, uuid4())
    with pytest.raises(DBAPIError) as exc:
        async with sessions.begin() as db:
            await set_tenant(db, ident["org"])
            await db.execute(
                text(
                    "INSERT INTO forge.sales_order_lines SELECT "
                    "(jsonb_populate_record(NULL::forge.sales_order_lines,"
                    "to_jsonb(l)||jsonb_build_object('id',:newid,'line_no',2))).* "
                    "FROM forge.sales_order_lines l WHERE order_id=:id"
                ),
                {"newid": uuid4(), "id": row["id"]},
            )
    assert exc.value.orig.sqlstate == "23514"
