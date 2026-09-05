from decimal import Decimal, localcontext

from sqlalchemy import text

from forge_erp.core.errors import Problem
from forge_erp.modules.funds.application import queries as funds_queries
from forge_erp.modules.purchasing.application import orders

COST_FIELDS = {
    "unit_price",
    "amount",
    "inventory_value_delta",
    "valuation_difference",
    "input_unit_cost",
}


def redact(ctx, row):
    return {
        k: v
        for k, v in row.items()
        if k != "organization_id"
        and (k not in COST_FIELDS or "product.cost.read" in ctx.permissions)
    }


async def order_detail(db, ctx, id, include_lines=True):
    ctx.require("purchase.read")
    row = await orders.load(db, ctx, id, share=True)
    lines = await orders.raw_lines(db, ctx, id)
    amounts = await orders.quantities(db, ctx, id)
    for line in lines:
        counts = amounts.get(line["id"], {"received": Decimal(0), "returned": Decimal(0)})
        line.update(
            received_base_qty=counts["received"],
            returned_base_qty=counts["returned"],
            remaining_base_qty=line["base_qty"] - counts["received"],
        )
        with localcontext() as dec:
            dec.prec = 50
            line["remaining_qty"] = line["remaining_base_qty"] / line["unit_to_base_factor"]
    row["receiving_status"] = (
        "RECEIVED"
        if all(x["remaining_base_qty"] == 0 for x in lines)
        else "PARTIAL"
        if any(x["received_base_qty"] for x in lines)
        else "UNRECEIVED"
    )
    if include_lines and {"funds.ap.read", "product.cost.read"} <= ctx.permissions:
        row["funds"] = await funds_queries.order_summary(db, ctx, "AP", id)
    row["lines"] = [redact(ctx, x) for x in lines] if include_lines else []
    return redact(ctx, row)


async def order_list(db, ctx, page, size, supplier=None, status=None, q=""):
    ctx.require("purchase.read")
    params = {"org": ctx.organization_id, "limit": size, "offset": (page - 1) * size}
    where = "organization_id=:org"
    if supplier:
        where += " AND supplier_id=:supplier"
        params["supplier"] = supplier
    if status:
        where += " AND status=:status"
        params["status"] = status
    if q:
        where += " AND (number ILIKE :q OR supplier_name ILIKE :q)"
        params["q"] = "%" + q.replace("%", r"\%").replace("_", r"\_") + "%"
    total = (
        await db.execute(text("SELECT count(*) FROM forge.purchase_orders WHERE " + where), params)
    ).scalar_one()
    ids = (
        (
            await db.execute(
                text(
                    "SELECT id FROM forge.purchase_orders WHERE "
                    + where
                    + " ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [await order_detail(db, ctx, id, False) for id in ids],
        "total": total,
        "page": page,
        "page_size": size,
    }


async def raw_document(db, ctx, id):
    row = (
        (
            await db.execute(
                text(
                    (
                        "SELECT d.*,p.order_id,p.kind,p.original_document_id,o.number AS "
                        "order_number,o.supplier_id,o.supplier_name,o.warehouse_name FROM "
                        "forge.purchase_documents p JOIN forge.inventory_documents d ON "
                        "(d.organization_id,d.id)=(p.organization_id,p.id) JOIN "
                        "forge.purchase_orders o ON "
                        "(o.organization_id,o.id)=(p.organization_id,p.order_id) WHERE "
                        "p.organization_id=:org AND p.id=:id"
                    )
                ),
                {"org": ctx.organization_id, "id": id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "NOT_FOUND", "采购单据不存在或无权访问")
    return dict(row)


async def raw_document_lines(db, ctx, id):
    return [
        dict(x)
        for x in (
            await db.execute(
                text("""SELECT l.*,p.order_id,p.order_line_id,
      p.receipt_line_id,p.unit_price,p.amount,
      coalesce((SELECT sum(rl.qty) FROM forge.purchase_document_lines rp
       JOIN forge.inventory_document_lines rl ON
(rl.organization_id,rl.id)=(rp.organization_id,rp.id)
       JOIN forge.inventory_documents rd ON
(rd.organization_id,rd.id)=(rp.organization_id,rp.document_id)
       WHERE rp.organization_id=p.organization_id AND rp.receipt_line_id=p.id AND
rd.status='POSTED'),0) AS returned_qty,
      coalesce((SELECT sum(rp.amount) FROM forge.purchase_document_lines rp
       JOIN forge.inventory_documents rd ON
(rd.organization_id,rd.id)=(rp.organization_id,rp.document_id)
       WHERE rp.organization_id=p.organization_id AND rp.receipt_line_id=p.id AND
rd.status='POSTED'),0) AS returned_amount,
      (SELECT m.value_delta FROM forge.inventory_movements m WHERE
m.organization_id=l.organization_id AND m.line_id=l.id AND m.kind IN ('RECEIVE','ISSUE') LIMIT 1) AS
inventory_value_delta
      FROM forge.purchase_document_lines p JOIN forge.inventory_document_lines l
      ON (l.organization_id,l.id)=(p.organization_id,p.id)
      WHERE p.organization_id=:org AND p.document_id=:id ORDER BY l.line_no"""),
                {"org": ctx.organization_id, "id": id},
            )
        ).mappings()
    ]


async def document_detail(db, ctx, id, include_lines=True):
    ctx.require("purchase.read")
    row = await raw_document(db, ctx, id)
    lines = await raw_document_lines(db, ctx, id)
    for line in lines:
        line["returnable_qty"] = (
            line["qty"] - line["returned_qty"]
            if row["kind"] == "RECEIPT" and row["status"] == "POSTED"
            else Decimal(0)
        )
        line["valuation_difference"] = (
            line["amount"] - abs(line["inventory_value_delta"])
            if row["kind"] == "RETURN" and line["inventory_value_delta"] is not None
            else None
        )
    with localcontext() as dec:
        dec.prec = 50
        row["amount"] = sum((x["amount"] for x in lines), Decimal(0))
    row["lines"] = [redact(ctx, x) for x in lines] if include_lines else []
    return redact(ctx, row)


async def document_list(db, ctx, page, size, kind=None, order=None):
    ctx.require("purchase.read")
    where = "p.organization_id=:org"
    params = {"org": ctx.organization_id, "limit": size, "offset": (page - 1) * size}
    if kind:
        where += " AND p.kind=:kind"
        params["kind"] = kind
    if order:
        where += " AND p.order_id=:order"
        params["order"] = order
    sql = (
        " FROM forge.purchase_documents p JOIN forge.inventory_documents d ON"
        " (d.organization_id,d.id)=(p.organization_id,p.id) WHERE "
    ) + where
    total = (await db.execute(text("SELECT count(*)" + sql), params)).scalar_one()
    ids = (
        (
            await db.execute(
                text(
                    "SELECT p.id"
                    + sql
                    + " ORDER BY d.created_at DESC,d.id DESC LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [await document_detail(db, ctx, id, False) for id in ids],
        "total": total,
        "page": page,
        "page_size": size,
    }


async def price_history(db, ctx, page, size, supplier=None, product=None):
    orders.require(ctx, "purchase.read")
    where = "p.organization_id=:org AND p.kind='RECEIPT' AND d.status='POSTED'"
    params = {"org": ctx.organization_id, "limit": size, "offset": (page - 1) * size}
    if supplier:
        where += " AND o.supplier_id=:supplier"
        params["supplier"] = supplier
    if product:
        where += " AND l.product_id=:product"
        params["product"] = product
    sql = (
        """ FROM forge.purchase_documents p JOIN forge.inventory_documents d
     ON (d.organization_id,d.id)=(p.organization_id,p.id) JOIN forge.purchase_orders o
     ON (o.organization_id,o.id)=(p.organization_id,p.order_id) JOIN
forge.purchase_document_lines pl
     ON (pl.organization_id,pl.document_id)=(p.organization_id,p.id) JOIN
forge.inventory_document_lines l
     ON (l.organization_id,l.id)=(pl.organization_id,pl.id) WHERE """
        + where
    )
    total = (await db.execute(text("SELECT count(*)" + sql), params)).scalar_one()
    rows = (
        (
            await db.execute(
                text(
                    (
                        "SELECT d.id AS document_id,d.number AS "
                        "document_number,o.supplier_id,o.supplier_name,l.product_id,l.product_label,l.unit_id,l.unit_label,pl.unit_price,l.qty,pl.amount,d.posted_at"
                    )
                    + sql
                    + " ORDER BY d.posted_at DESC,d.id DESC,l.id DESC LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return {"items": [dict(x) for x in rows], "total": total, "page": page, "page_size": size}
