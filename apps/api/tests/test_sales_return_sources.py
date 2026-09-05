"""Sales return and reversal trust boundaries, below the HTTP command layer."""

from decimal import Decimal as D
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_catalog import catalog_client as catalog_client
from test_sales_orders import action, opening, so
from test_sales_orders import sale as sale
from test_sales_reservation_sources import shipment_fixture

from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions, set_tenant
from forge_erp.core.errors import Problem
from forge_erp.modules.inventory.application.engine import InventoryEngine


def context(ident, *permissions):
    return RuntimeContext(ident["org"], ident["user"], frozenset(permissions), "return-source-test")


@pytest.fixture
async def posted_source(catalog_client, sale, identities):
    c, ident = catalog_client, identities[0]
    await opening(c, sale, "10")
    order = await so(c, sale)
    confirmed = (await action(c, order)).json()
    key = (UUID(sale["warehouse_id"]), UUID(sale["lines"][0]["product_id"]))
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        doc, line, source = await shipment_fixture(db, ident, order)
        engine = InventoryEngine(db, context(ident, "sales.ship"), "sales.ship")
        await engine.lock([key])
        movement = await engine.change(
            key, line, doc, "ISSUE", D("2"), reservation_id=source["reservation_id"]
        )
        await mark_posted(db, ident, doc)
    return ident, key, order, confirmed, doc, line, source, movement


async def mark_posted(db, ident, doc):
    await db.execute(
        text(
            "UPDATE forge.inventory_documents SET status='POSTED',posted_at=now(),"
            "version=version+1 WHERE organization_id=:org AND id=:doc"
        ),
        ident | {"doc": doc},
    )


async def return_fixture(db, fixture, qty="1", amount=None, cost=None):
    ident, _, order, _, original_doc, original_line, source, _ = fixture
    doc, line = uuid4(), uuid4()
    params = (
        source
        | ident
        | {
            "doc": doc,
            "line": line,
            "order": order["id"],
            "original_doc": original_doc,
            "original_line": original_line,
            "qty": D(qty),
            "amount": D(amount) if amount is not None else D(qty) * source["unit_price"],
            "cost": D(cost) if cost is not None else D(qty) * 11,
        }
    )
    for sql in [
        """INSERT INTO forge.inventory_documents
        (id,organization_id,number,type,reason,warehouse_id,created_by)
        SELECT :doc,:org,CAST(:doc AS text),'SALES_RETURN','engine return',warehouse_id,:user
        FROM forge.sales_orders WHERE organization_id=:org AND id=:order""",
        """INSERT INTO forge.sales_documents
        (id,organization_id,order_id,kind,original_document_id)
        VALUES(:doc,:org,:order,'RETURN',:original_doc)""",
        """INSERT INTO forge.inventory_document_lines
        (id,organization_id,document_id,line_no,product_id,unit_id,product_label,unit_label,
         qty,unit_to_base_factor,base_qty,conversion_version,direction,original_line_id)
        VALUES(:line,:org,:doc,1,:product_id,:unit_id,:product_label,:unit_label,
         :qty,:unit_to_base_factor,:qty*:unit_to_base_factor,:conversion_version,'IN',:original_line)""",
        """INSERT INTO forge.sales_document_lines
        (id,organization_id,document_id,order_id,order_line_id,shipment_line_id,
         unit_price,amount,return_cost)
        VALUES(:line,:org,:doc,:order,:id,:original_line,:unit_price,:amount,:cost)""",
    ]:
        await db.execute(text(sql), params)
    return doc, line


async def reversal(db, ident, doc):
    return (
        await db.execute(
            text(
                "INSERT INTO forge.inventory_reversals"
                "(organization_id,document_id,reason,actor_id,request_id) "
                "VALUES(:org,:doc,'engine reverse',:user,'engine-reverse') RETURNING id"
            ),
            ident | {"doc": doc},
        )
    ).scalar_one()


async def mark_reversed(db, ident, doc, rid):
    await db.execute(
        text(
            "UPDATE forge.inventory_documents SET status='REVERSED',reversed_at=now(),"
            "reversal_id=:rid,version=version+1 WHERE organization_id=:org AND id=:doc"
        ),
        ident | {"doc": doc, "rid": rid},
    )


async def assert_reconciled(db, ident, key):
    engine = InventoryEngine(
        db, context(ident, "inventory.reconcile", "product.cost.read"), "inventory.reconcile"
    )
    await engine.lock([key])
    assert all(not row["differences"] for row in await engine.reconcile())


async def test_sales_return_exact_value_then_reverse_preserves_reservation(posted_source):
    ident, key, _, _, _, _, _, _ = posted_source
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        doc, line = await return_fixture(db, posted_source)
        engine = InventoryEngine(
            db, context(ident, "sales.return", "sales.reverse"), "sales.return"
        )
        await engine.lock([key])
        movement = await engine.change(key, line, doc, "RECEIVE", D("1"), value=D("11"))
        assert movement["base_qty"] == 1 and movement["value_delta"] == 11
        assert movement["reservation_id"] is None and movement["reserved_qty_delta"] == 0
        assert engine.state(key).qty == 9 and engine.state(key).reserved == 5
        await mark_posted(db, ident, doc)
        rid = await reversal(db, ident, doc)
        reverse = await engine.reverse(movement, rid)
        assert reverse["base_qty"] == -1 and reverse["value_delta"] == -11
        assert reverse["reservation_id"] is None and reverse["reserved_qty_delta"] == 0
        assert engine.state(key).qty == 8 and engine.state(key).reserved == 5
        await mark_reversed(db, ident, doc, rid)
        await assert_reconciled(db, ident, key)


async def test_sales_shipment_reverse_restores_original_reservation(posted_source):
    ident, key, _, _, doc, _, source, movement = posted_source
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        engine = InventoryEngine(db, context(ident, "sales.ship", "sales.reverse"), "sales.ship")
        await engine.lock([key])
        rid = await reversal(db, ident, doc)
        reverse = await engine.reverse(movement, rid)
        assert reverse["base_qty"] == 2 and reverse["value_delta"] == 22
        assert reverse["reservation_id"] == source["reservation_id"]
        assert reverse["reserved_qty_delta"] == 2 and reverse["reservation_consumed_delta"] == -2
        assert engine.state(key).qty == 10 and engine.state(key).reserved == 7
        await mark_reversed(db, ident, doc, rid)
        await assert_reconciled(db, ident, key)


@pytest.mark.parametrize(
    "attempt", ["value", "unit_cost", "reservation", "qty", "operation", "kind", "generic", "ship"]
)
async def test_sales_return_engine_rejects_untrusted_operation(posted_source, attempt):
    ident, key, _, _, _, _, source, _ = posted_source
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        doc, line = await return_fixture(db, posted_source)
        permission = {"generic": "inventory.adjust", "ship": "sales.ship"}.get(
            attempt, "sales.return"
        )
        engine = InventoryEngine(
            db, context(ident, "sales.return", "sales.ship", "inventory.adjust"), permission
        )
        await engine.lock([key])
        with pytest.raises(Problem) as error:
            await engine.change(
                key,
                line,
                uuid4() if attempt == "operation" else doc,
                "ISSUE" if attempt == "kind" else "RECEIVE",
                D("2") if attempt == "qty" else D("1"),
                value=D("12") if attempt == "value" else D("11"),
                cost=D("11") if attempt == "unit_cost" else None,
                reservation_id=source["reservation_id"] if attempt == "reservation" else None,
            )
        assert error.value.code in {"INVALID_SOURCE", "SALES_COMMAND_REQUIRED"}
        assert engine.state(key).qty == 8 and engine.state(key).reserved == 5
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.inventory_movements WHERE document_id=:doc"),
                {"doc": doc},
            )
        ).scalar_one() == 0


@pytest.mark.parametrize("field", ["original", "factor", "missing_association", "missing_cost"])
async def test_sales_return_deferred_source_constraints(posted_source, field):
    ident, _, _, _, _, _, source, _ = posted_source
    with pytest.raises(DBAPIError) as error:
        async with sessions.begin() as db:
            await set_tenant(db, ident["org"])
            _, line = await return_fixture(db, posted_source)
            if field == "original":
                await db.execute(
                    text(
                        "UPDATE forge.inventory_document_lines "
                        "SET original_line_id=:id WHERE id=:line"
                    ),
                    {"id": source["source_id"], "line": line},
                )
            elif field == "factor":
                await db.execute(
                    text(
                        "UPDATE forge.inventory_document_lines "
                        "SET unit_to_base_factor=2,base_qty=2 WHERE id=:line"
                    ),
                    {"line": line},
                )
            else:
                # Draft sales lines are replaced, never changed in place.
                saved = (
                    await db.execute(
                        text("SELECT to_jsonb(l) FROM forge.sales_document_lines l WHERE id=:line"),
                        {"line": line},
                    )
                ).scalar_one()
                await db.execute(
                    text("DELETE FROM forge.sales_document_lines WHERE id=:line"), {"line": line}
                )
                if field == "missing_cost":
                    import json

                    saved["return_cost"] = None
                    await db.execute(
                        text(
                            "INSERT INTO forge.sales_document_lines SELECT * FROM "
                            "jsonb_populate_record(NULL::forge.sales_document_lines,"
                            "CAST(:row AS jsonb))"
                        ),
                        {"row": json.dumps(saved)},
                    )
    assert error.value.orig.sqlstate == "23514"


async def test_sales_return_engine_recomputes_saved_cost_snapshot(posted_source):
    ident, key, *_ = posted_source
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        doc, line = await return_fixture(db, posted_source, cost="12")
        engine = InventoryEngine(db, context(ident, "sales.return"), "sales.return")
        await engine.lock([key])
        with pytest.raises(Problem) as error:
            await engine.change(key, line, doc, "RECEIVE", D("1"), value=D("12"))
        assert error.value.code == "RETURN_QUOTE_CHANGED"
        assert engine.state(key).qty == 8


async def test_sales_return_engine_enforces_cumulative_posted_quota(posted_source):
    ident, key, *_ = posted_source
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        engine = InventoryEngine(db, context(ident, "sales.return"), "sales.return")
        await engine.lock([key])
        first_doc, first_line = await return_fixture(db, posted_source, "1.5")
        await engine.change(key, first_line, first_doc, "RECEIVE", D("1.5"), value=D("16.5"))
        await mark_posted(db, ident, first_doc)
        second_doc, second_line = await return_fixture(db, posted_source, "1")
        with pytest.raises(Problem) as error:
            await engine.change(key, second_line, second_doc, "RECEIVE", D("1"), value=D("11"))
        assert error.value.code == "OVER_RETURN"
        assert engine.state(key).qty == D("9.5")


@pytest.mark.parametrize("field", ["value_delta", "base_qty", "sequence"])
async def test_sales_reverse_rejects_forged_movement_mapping(posted_source, field):
    ident, key, _, _, doc, _, _, movement = posted_source
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        engine = InventoryEngine(db, context(ident, "sales.ship", "sales.reverse"), "sales.ship")
        await engine.lock([key])
        rid = await reversal(db, ident, doc)
        with pytest.raises(Problem) as error:
            await engine.reverse(movement | {field: movement[field] + 1}, rid)
        assert error.value.code == "INVALID_SOURCE"
        assert engine.state(key).qty == 8 and engine.state(key).reserved == 5


@pytest.mark.parametrize("permission", ["inventory.reverse", "sales.return"])
async def test_sales_reverse_requires_matching_capability(posted_source, permission):
    ident, key, _, _, doc, _, _, movement = posted_source
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        engine = InventoryEngine(db, context(ident, permission, "sales.reverse"), permission)
        await engine.lock([key])
        rid = await reversal(db, ident, doc)
        with pytest.raises(Problem) as error:
            await engine.reverse(movement, rid)
        assert error.value.code == "SALES_COMMAND_REQUIRED"


async def test_sales_reverse_requires_extra_reverse_permission(posted_source):
    ident, key, _, _, doc, _, _, movement = posted_source
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        engine = InventoryEngine(db, context(ident, "sales.ship"), "sales.ship")
        await engine.lock([key])
        rid = await reversal(db, ident, doc)
        with pytest.raises(Problem) as error:
            await engine.reverse(movement, rid)
        assert error.value.status == 403


async def test_sales_shipment_reverse_rejects_closed_order(catalog_client, posted_source):
    ident, key, _, confirmed, doc, _, _, movement = posted_source
    assert (await action(catalog_client, confirmed, "close", "停止发货")).status_code == 200
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        engine = InventoryEngine(db, context(ident, "sales.ship", "sales.reverse"), "sales.ship")
        await engine.lock([key])
        rid = await reversal(db, ident, doc)
        with pytest.raises(Problem) as error:
            await engine.reverse(movement, rid)
        assert error.value.code == "REVERSAL_DEPENDENCY_CONFLICT"
        assert engine.state(key).reserved == 0


async def test_sales_return_reversal_does_not_make_original_shipment_last(posted_source):
    ident, key, _, _, original_doc, _, _, original_movement = posted_source
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        doc, line = await return_fixture(db, posted_source)
        permissions = ("sales.ship", "sales.return", "sales.reverse")
        engine = InventoryEngine(db, context(ident, *permissions), "sales.return")
        await engine.lock([key])
        movement = await engine.change(key, line, doc, "RECEIVE", D("1"), value=D("11"))
        await mark_posted(db, ident, doc)
        rid = await reversal(db, ident, doc)
        await engine.reverse(movement, rid)
        await mark_reversed(db, ident, doc, rid)
        original_rid = await reversal(db, ident, original_doc)
        ship_engine = InventoryEngine(db, context(ident, *permissions), "sales.ship")
        await ship_engine.lock([key])
        with pytest.raises(Problem) as error:
            await ship_engine.reverse(original_movement, original_rid)
        assert error.value.code == "REVERSAL_DEPENDENCY_CONFLICT"
        await assert_reconciled(db, ident, key)


async def test_sales_return_source_predicate_preserves_invoker_rls(posted_source, identities):
    ident, _, _, _, _, _, _, _ = posted_source
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        _, line = await return_fixture(db, posted_source)
        assert (
            await db.execute(
                text("SELECT forge.sales_return_source_valid(:org,:line)"), ident | {"line": line}
            )
        ).scalar_one()
        assert (
            await db.execute(
                text(
                    "SELECT prosecdef FROM pg_proc "
                    "WHERE oid='forge.sales_return_source_valid(uuid,uuid)'::regprocedure"
                )
            )
        ).scalar_one() is False
    async with sessions.begin() as db:
        await set_tenant(db, identities[1]["org"])
        assert not (
            await db.execute(
                text("SELECT forge.sales_return_source_valid(:org,:line)"), ident | {"line": line}
            )
        ).scalar_one()
