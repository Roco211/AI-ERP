"""Exercise the engine trust boundary independently of sales HTTP commands."""

from decimal import Decimal as D
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_catalog import catalog_client as catalog_client
from test_inventory_engine import run_change
from test_inventory_engine import stock as stock
from test_sales_orders import action, opening, so
from test_sales_orders import sale as sale

from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions, set_tenant
from forge_erp.core.errors import Problem
from forge_erp.modules.inventory.application.engine import InventoryEngine


async def shipment_fixture(db, ident, order):
    source = dict(
        (
            await db.execute(
                text("""SELECT ol.*,r.line_id AS source_id,r.id AS reservation_id
                FROM forge.sales_order_lines ol JOIN forge.sales_document_lines sl
                ON (sl.organization_id,sl.order_id,sl.order_line_id)=
                   (ol.organization_id,ol.order_id,ol.id)
                JOIN forge.sales_documents sd ON
                   (sd.organization_id,sd.id)=(sl.organization_id,sl.document_id)
                JOIN forge.inventory_reservations r ON
                   (r.organization_id,r.line_id)=(sl.organization_id,sl.id)
                WHERE ol.organization_id=:org AND ol.order_id=:order
                 AND sd.kind='RESERVATION'"""),
                ident | {"order": order["id"]},
            )
        )
        .mappings()
        .one()
    )
    doc, line = uuid4(), uuid4()
    params = source | ident | {"doc": doc, "line": line, "order": order["id"], "qty": D("2")}
    for sql in [
        """INSERT INTO forge.inventory_documents
        (id,organization_id,number,type,reason,warehouse_id,created_by)
        SELECT :doc,:org,CAST(:doc AS text),'SALES_SHIPMENT','engine fixture',warehouse_id,:user
        FROM forge.sales_orders WHERE organization_id=:org AND id=:order""",
        """INSERT INTO forge.sales_documents(id,organization_id,order_id,kind)
        VALUES(:doc,:org,:order,'SHIPMENT')""",
        """INSERT INTO forge.inventory_document_lines
        (id,organization_id,document_id,line_no,product_id,unit_id,product_label,unit_label,
         qty,unit_to_base_factor,base_qty,conversion_version,direction,reservation_source_line_id)
        VALUES(:line,:org,:doc,1,:product_id,:unit_id,:product_label,:unit_label,
         :qty,:unit_to_base_factor,:qty*:unit_to_base_factor,:conversion_version,'OUT',:source_id)""",
        """INSERT INTO forge.sales_document_lines
        (id,organization_id,document_id,order_id,order_line_id,unit_price,amount)
        VALUES(:line,:org,:doc,:order,:id,:unit_price,round(:qty*:unit_price,4))""",
    ]:
        await db.execute(text(sql), params)
    return doc, line, source


def engine_context(ident, *extra):
    return RuntimeContext(
        ident["org"], ident["user"], frozenset({"sales.ship", *extra}), "engine-source-test"
    )


async def test_sales_engine_distinct_source_consumption_and_reconciliation(
    catalog_client, sale, identities
):
    c, ident = catalog_client, identities[0]
    await opening(c, sale)
    order = await so(c, sale)
    confirmed = (await action(c, order)).json()
    key = (UUID(sale["warehouse_id"]), UUID(sale["lines"][0]["product_id"]))
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        doc, line, source = await shipment_fixture(db, ident, order)
        e = InventoryEngine(db, engine_context(ident), "sales.ship")
        await e.lock([key])
        m = await e.change(key, line, doc, "ISSUE", D("2"), reservation_id=source["reservation_id"])
        assert m["line_id"] == line != source["source_id"]
        assert m["reservation_id"] == source["reservation_id"]
        assert m["base_qty"] == -2 and m["value_delta"] == -22
        assert m["reservation_consumed_delta"] == 2 and m["reserved_qty_delta"] == -2
        assert e.state(key).qty == 8 and e.state(key).reserved == 5
        await db.execute(
            text("""UPDATE forge.inventory_documents SET status='POSTED',posted_at=now(),
                 version=version+1 WHERE organization_id=:org AND id=:doc"""),
            ident | {"doc": doc},
        )
    closed = await action(c, confirmed, "close", "释放未发部分")
    assert closed.status_code == 200, closed.text
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        e = InventoryEngine(
            db,
            engine_context(ident, "inventory.reconcile", "product.cost.read"),
            "inventory.reconcile",
        )
        await e.lock([key])
        assert e.state(key).qty == 8 and e.state(key).reserved == 0
        assert all(not row["differences"] for row in await e.reconcile())
        r = (
            (
                await db.execute(
                    text("SELECT * FROM forge.inventory_reservations WHERE id=:id"),
                    {"id": source["reservation_id"]},
                )
            )
            .mappings()
            .one()
        )
        assert (r["reserved_qty"], r["consumed_qty"], r["released_qty"], r["remaining_qty"]) == (
            7,
            2,
            5,
            0,
        )


@pytest.mark.parametrize(
    "attempt",
    [
        "no_reservation",
        "no_link",
        "forged_link",
        "wrong_reservation",
        "wrong_qty",
        "wrong_op",
        "receive",
        "generic",
        "reverse",
    ],
)
async def test_sales_engine_cannot_bypass_own_reservation(
    catalog_client, sale, identities, attempt
):
    c, ident = catalog_client, identities[0]
    await opening(c, sale, "20")
    order, other = await so(c, sale), await so(c, sale)
    assert (await action(c, order)).status_code == 200
    assert (await action(c, other)).status_code == 200
    key = (UUID(sale["warehouse_id"]), UUID(sale["lines"][0]["product_id"]))
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        doc, line, source = await shipment_fixture(db, ident, order)
        _, _, another = await shipment_fixture(db, ident, other)
        if attempt in {"no_link", "forged_link"}:
            await db.execute(
                text(
                    "UPDATE forge.inventory_document_lines "
                    "SET reservation_source_line_id=:source WHERE id=:line"
                ),
                {
                    "line": line,
                    "source": another["source_id"] if attempt == "forged_link" else None,
                },
            )
        permission = "inventory.adjust" if attempt == "generic" else "sales.ship"
        e = InventoryEngine(
            db, engine_context(ident, "inventory.adjust", "inventory.reverse"), permission
        )
        await e.lock([key])
        with pytest.raises(Problem) as exc:
            if attempt == "reverse":
                await e.reverse(
                    {"line_id": line, "warehouse_id": key[0], "product_id": key[1]}, uuid4()
                )
            else:
                await e.change(
                    key,
                    line,
                    uuid4() if attempt == "wrong_op" else doc,
                    "RECEIVE" if attempt == "receive" else "ISSUE",
                    D("3") if attempt == "wrong_qty" else D("2"),
                    reservation_id=(
                        None
                        if attempt == "no_reservation"
                        else another["reservation_id"]
                        if attempt == "wrong_reservation"
                        else source["reservation_id"]
                    ),
                )
        assert exc.value.code in {"INVALID_SOURCE", "NOT_FOUND", "SALES_COMMAND_REQUIRED"}
        if attempt in {"no_link", "forged_link"}:
            # The attempted engine mutation failed. Restore the test-only draft
            # forgery so commit-time guards can independently verify its normal source.
            await db.execute(
                text(
                    "UPDATE forge.inventory_document_lines "
                    "SET reservation_source_line_id=:source WHERE id=:line"
                ),
                {"line": line, "source": source["source_id"]},
            )
        assert e.state(key).qty == 20 and e.state(key).reserved == 14
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.inventory_movements WHERE kind='ISSUE'")
            )
        ).scalar_one() == 0


@pytest.mark.parametrize("field", ["other_order", "factor", "price", "missing_association"])
async def test_sales_source_deferred_sql_guard(catalog_client, sale, identities, field):
    c, ident = catalog_client, identities[0]
    await opening(c, sale, "20")
    order, other = await so(c, sale), await so(c, sale)
    assert (await action(c, order)).status_code == 200
    assert (await action(c, other)).status_code == 200
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        doc, line, source = await shipment_fixture(db, ident, order)
        _, _, another = await shipment_fixture(db, ident, other)
    with pytest.raises(DBAPIError) as exc:
        async with sessions.begin() as db:
            await set_tenant(db, ident["org"])
            params = {"line": line, "source": another["source_id"]}
            if field == "other_order":
                sql = (
                    "UPDATE forge.inventory_document_lines SET "
                    "reservation_source_line_id=:source WHERE id=:line"
                )
            elif field == "factor":
                sql = (
                    "UPDATE forge.inventory_document_lines SET "
                    "unit_to_base_factor=2,base_qty=qty*2 WHERE id=:line"
                )
            else:
                # Sales lines have insert/delete draft semantics, never in-place edits.
                snapshot = (
                    await db.execute(
                        text("SELECT to_jsonb(l) FROM forge.sales_document_lines l WHERE id=:line"),
                        params,
                    )
                ).scalar_one()
                await db.execute(
                    text("DELETE FROM forge.sales_document_lines WHERE id=:line"), params
                )
                if field == "price":
                    import json

                    snapshot["unit_price"] = "1"
                    snapshot["amount"] = "2"
                    await db.execute(
                        text(
                            "INSERT INTO forge.sales_document_lines SELECT * FROM "
                            "jsonb_populate_record(NULL::forge.sales_document_lines,"
                            "CAST(:row AS jsonb))"
                        ),
                        {"row": json.dumps(snapshot)},
                    )
                sql = "SELECT 1"
            await db.execute(text(sql), params)
    assert exc.value.orig.sqlstate == "23514"
    # Failed transaction preserved the legitimate source, and no stock was consumed.
    async with sessions.begin() as db:
        await set_tenant(db, ident["org"])
        assert (
            await db.execute(
                text("SELECT forge.sales_shipment_source_valid(:org,:line)"), ident | {"line": line}
            )
        ).scalar_one()


async def test_legacy_engine_reservations_still_require_same_line(stock):
    ctx, key, line, doc, _ = stock
    await run_change(stock, "RECEIVE", "10", "3")
    movement, _ = await run_change(stock, "RESERVE", "5")
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        replacement_doc, replacement_line = uuid4(), uuid4()
        params = {"doc": doc, "line": line, "newdoc": replacement_doc, "newline": replacement_line}
        for sql in [
            """INSERT INTO forge.inventory_documents
            SELECT (jsonb_populate_record(NULL::forge.inventory_documents,
             to_jsonb(d)||jsonb_build_object('id',:newdoc,'number',CAST(:newdoc AS text)))).*
            FROM forge.inventory_documents d WHERE id=:doc""",
            """INSERT INTO forge.inventory_document_lines
            SELECT (jsonb_populate_record(NULL::forge.inventory_document_lines,
             to_jsonb(l)||jsonb_build_object('id',:newline,'document_id',:newdoc))).*
            FROM forge.inventory_document_lines l WHERE id=:line""",
        ]:
            await db.execute(text(sql), params)
        e = InventoryEngine(db, ctx)
        await e.lock([key])
        with pytest.raises(Problem) as exc:
            await e.change(
                key,
                replacement_line,
                uuid4(),
                "ISSUE",
                D("1"),
                reservation_id=movement["reservation_id"],
            )
        assert exc.value.code == "NOT_FOUND"
        assert e.state(key).qty == 10 and e.state(key).reserved == 5
