"""Sales reservation commands. InventoryEngine alone writes stock projections."""

from uuid import uuid4

from sqlalchemy import text

from forge_erp.core.errors import Problem
from forge_erp.modules.inventory.application.engine import InventoryEngine


async def reserve(db, ctx, order, lines):
    doc = uuid4()
    params = {
        "org": ctx.organization_id,
        "doc": doc,
        "order": order["id"],
        "wh": order["warehouse_id"],
        "actor": ctx.user_id,
        "number": "SR-" + uuid4().hex[:16].upper(),
        "reason": order["reason"],
    }
    await db.execute(
        text("""INSERT INTO forge.inventory_documents
      (id,organization_id,number,type,reason,warehouse_id,created_by)
      VALUES(:doc,:org,:number,'SALES_RESERVATION',:reason,:wh,:actor)"""),
        params,
    )
    await db.execute(
        text("""INSERT INTO forge.sales_documents(id,organization_id,order_id,kind)
      VALUES(:doc,:org,:order,'RESERVATION')"""),
        params,
    )
    sources = []
    for line in lines:
        source = uuid4()
        await db.execute(
            text("""INSERT INTO forge.inventory_document_lines
          (id,organization_id,document_id,line_no,product_id,unit_id,product_label,unit_label,
           qty,unit_to_base_factor,base_qty,conversion_version,direction)
          VALUES(:source,:org,:doc,:line_no,:product_id,:unit_id,:product_label,:unit_label,
           :qty,:unit_to_base_factor,:base_qty,:conversion_version,'OUT')"""),
            line | params | {"source": source},
        )
        await db.execute(
            text("""INSERT INTO forge.sales_document_lines
          (id,organization_id,document_id,order_id,order_line_id,unit_price,amount)
          VALUES(:source,:org,:doc,:order,:id,:unit_price,:amount)"""),
            line | params | {"source": source},
        )
        sources.append((line, source))
    engine = InventoryEngine(db, ctx, "sales.order.confirm")
    await engine.lock([(order["warehouse_id"], x["product_id"]) for x in lines])
    for line, source in sources:
        await engine.change(
            (order["warehouse_id"], line["product_id"]), source, doc, "RESERVE", line["base_qty"]
        )
    await db.execute(
        text("""UPDATE forge.inventory_documents SET status='POSTED',
      posted_at=now(),version=version+1 WHERE organization_id=:org AND id=:doc"""),
        params,
    )


async def remaining(db, ctx, order):
    return {
        r["order_line_id"]: r["remaining_qty"]
        for r in (
            await db.execute(
                text("""
      SELECT sl.order_line_id,r.remaining_qty FROM forge.sales_document_lines sl
      JOIN forge.sales_documents sd ON
(sd.organization_id,sd.id)=(sl.organization_id,sl.document_id)
      JOIN forge.inventory_reservations r ON
(r.organization_id,r.line_id)=(sl.organization_id,sl.id)
      WHERE sl.organization_id=:org AND sl.order_id=:order AND sd.kind='RESERVATION'
      """),
                {"org": ctx.organization_id, "order": order},
            )
        ).mappings()
    }


async def release(db, ctx, order, action):
    params = {"org": ctx.organization_id, "order": order["id"]}
    # The caller already holds the order lock: confirm, close, cancel and future shipment serialize.
    doc = (
        await db.execute(
            text("""SELECT d.id FROM forge.sales_documents sd
      JOIN forge.inventory_documents d ON (d.organization_id,d.id)=(sd.organization_id,sd.id)
      WHERE sd.organization_id=:org AND sd.order_id=:order AND sd.kind='RESERVATION'
      AND d.status='POSTED' FOR UPDATE OF d"""),
            params,
        )
    ).scalar_one_or_none()
    if doc is None:
        raise Problem(409, "INVALID_SOURCE", "订单占用来源不完整，请核对")
    rows = (
        (
            await db.execute(
                text("""SELECT l.id,l.product_id FROM forge.inventory_document_lines l
      WHERE l.organization_id=:org AND l.document_id=:doc ORDER BY l.product_id"""),
                params | {"doc": doc},
            )
        )
        .mappings()
        .all()
    )
    engine = InventoryEngine(db, ctx, "sales.order." + action)
    await engine.lock([(order["warehouse_id"], x["product_id"]) for x in rows], historical=True)
    operation = uuid4()
    for line in rows:
        reservation = (
            (
                await db.execute(
                    text("""SELECT * FROM forge.inventory_reservations
          WHERE organization_id=:org AND line_id=:line FOR UPDATE"""),
                    params | {"line": line["id"]},
                )
            )
            .mappings()
            .one()
        )
        if reservation["remaining_qty"]:
            await engine.change(
                (order["warehouse_id"], line["product_id"]),
                line["id"],
                operation,
                "RELEASE",
                reservation["remaining_qty"],
                reservation_id=reservation["id"],
            )
