from decimal import Decimal, localcontext

from sqlalchemy import text

from forge_erp.core.errors import Problem
from forge_erp.modules.sales.application import orders

PRICE_FIELDS = {
    "unit_price",
    "amount",
    "price_source",
    "pricing_mode",
}


COST_FIELDS = {"actual_cost", "inventory_value_delta", "input_unit_cost", "gross_margin"}


def redact(ctx, row):
    return {
        k: v
        for k, v in row.items()
        if k != "organization_id"
        and (k not in PRICE_FIELDS or "product.price.read" in ctx.permissions)
        and (
            k not in COST_FIELDS
            or {"sales.read", "product.price.read", "product.cost.read"} <= ctx.permissions
        )
    }


async def order_detail(db, ctx, id, include_lines=True):
    ctx.require("sales.read")
    row = await orders.load(db, ctx, id, share=True)
    lines = await orders.raw_lines(db, ctx, id)
    amounts = await orders.quantities(db, ctx, id)
    reserved = await orders.reservations.remaining(db, ctx, id)
    for line in lines:
        counts = amounts.get(line["id"], {"shipped": Decimal(0), "returned": Decimal(0)})
        line.update(
            shipped_base_qty=counts["shipped"],
            returned_base_qty=counts["returned"],
            remaining_base_qty=line["base_qty"] - counts["shipped"],
        )
        line["reserved_base_qty"] = reserved.get(line["id"], Decimal(0))
        line["executable_base_qty"] = (
            line["reserved_base_qty"] if row["status"] == "CONFIRMED" else Decimal(0)
        )
    row["fulfillment_status"] = (
        "FULFILLED"
        if all(x["remaining_base_qty"] == 0 for x in lines)
        else "PARTIAL"
        if any(x["shipped_base_qty"] for x in lines)
        else "UNFULFILLED"
    )
    row["lines"] = [redact(ctx, x) for x in lines] if include_lines else []
    return redact(ctx, row)


async def order_list(db, ctx, page, size, customer=None, status=None, q=""):
    ctx.require("sales.read")
    params = {"org": ctx.organization_id, "limit": size, "offset": (page - 1) * size}
    where = "organization_id=:org"
    if customer:
        where += " AND customer_id=:customer"
        params["customer"] = customer
    if status:
        where += " AND status=:status"
        params["status"] = status
    if q:
        where += " AND (number ILIKE :q OR customer_name ILIKE :q)"
        params["q"] = "%" + q.replace("%", r"\%").replace("_", r"\_") + "%"
    ids = (
        (
            await db.execute(
                text(
                    "SELECT id FROM forge.sales_orders WHERE "
                    + where
                    + " ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        )
        .scalars()
        .all()
    )
    for order_id in sorted(ids):
        await orders.load(db, ctx, order_id, share=True)
    # A mutation may commit while we acquire shared locks. Reapply filters to the selected IDs.
    matching = (
        set(
            (
                await db.execute(
                    text(
                        "SELECT id FROM forge.sales_orders WHERE "
                        + where
                        + " AND id=ANY(:selected)"
                    ),
                    params | {"selected": ids},
                )
            ).scalars()
        )
        if ids
        else set()
    )
    total = (
        await db.execute(text("SELECT count(*) FROM forge.sales_orders WHERE " + where), params)
    ).scalar_one()
    return {
        "items": [await order_detail(db, ctx, id, False) for id in ids if id in matching],
        "total": total,
        "page": page,
        "page_size": size,
    }


async def raw_document(db, ctx, id):
    row = (
        (
            await db.execute(
                text("""SELECT d.*,sd.order_id,sd.kind,
        o.number AS order_number,o.customer_id,o.customer_name,o.warehouse_name
        FROM forge.sales_documents sd JOIN forge.inventory_documents d
          ON (d.organization_id,d.id)=(sd.organization_id,sd.id)
        JOIN forge.sales_orders o ON (o.organization_id,o.id)=(sd.organization_id,sd.order_id)
        WHERE sd.organization_id=:org AND sd.id=:id AND sd.kind='SHIPMENT'
          AND d.type='SALES_SHIPMENT'"""),
                {"org": ctx.organization_id, "id": id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "NOT_FOUND", "销售出库单不存在或无权访问")
    return dict(row)


async def raw_document_lines(db, ctx, id):
    return [
        dict(row)
        for row in (
            await db.execute(
                text("""SELECT l.*,sl.order_id,
        sl.order_line_id,sl.unit_price,sl.amount,
        (SELECT -m.value_delta FROM forge.inventory_movements m
          WHERE m.organization_id=l.organization_id AND m.line_id=l.id
            AND m.document_id=l.document_id AND m.operation_id=l.document_id
            AND m.kind='ISSUE') AS actual_cost
        FROM forge.sales_document_lines sl JOIN forge.inventory_document_lines l
          ON (l.organization_id,l.id)=(sl.organization_id,sl.id)
        WHERE sl.organization_id=:org AND sl.document_id=:id ORDER BY l.line_no"""),
                {"org": ctx.organization_id, "id": id},
            )
        ).mappings()
    ]


async def document_detail(db, ctx, id, include_lines=True):
    ctx.require("sales.read")
    initial = await raw_document(db, ctx, id)
    # All sales writes acquire this order FOR UPDATE. Keep header, lines and cost in one state.
    await orders.load(db, ctx, initial["order_id"], share=True)
    row = await raw_document(db, ctx, id)
    lines = await raw_document_lines(db, ctx, id)
    with localcontext() as dec:
        dec.prec = 50
        row["amount"] = sum((x["amount"] for x in lines), Decimal(0))
        row["actual_cost"] = (
            sum((x["actual_cost"] for x in lines), Decimal(0))
            if row["status"] != "DRAFT"
            and lines
            and all(x["actual_cost"] is not None for x in lines)
            else None
        )
    row["lines"] = [redact(ctx, x) for x in lines] if include_lines else []
    return redact(ctx, row)


async def document_list(db, ctx, page, size, order=None, customer=None, status=None, q=""):
    ctx.require("sales.read")
    params = {"org": ctx.organization_id, "limit": size, "offset": (page - 1) * size}
    where = "sd.organization_id=:org AND sd.kind='SHIPMENT' AND d.type='SALES_SHIPMENT'"
    for field, value, column in [
        ("order", order, "sd.order_id"),
        ("customer", customer, "o.customer_id"),
        ("status", status, "d.status"),
    ]:
        if value is not None:
            where += f" AND {column}=:{field}"
            params[field] = value
    if q:
        where += " AND (d.number ILIKE :q OR o.number ILIKE :q OR o.customer_name ILIKE :q)"
        params["q"] = "%" + q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
    sql = (
        """ FROM forge.sales_documents sd JOIN forge.inventory_documents d
        ON (d.organization_id,d.id)=(sd.organization_id,sd.id)
        JOIN forge.sales_orders o ON (o.organization_id,o.id)=(sd.organization_id,sd.order_id)
        WHERE """
        + where
    )
    rows = (
        (
            await db.execute(
                text(
                    "SELECT d.id,sd.order_id"
                    + sql
                    + " ORDER BY d.created_at DESC,d.id DESC LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    for order_id in sorted({x["order_id"] for x in rows}):
        await orders.load(db, ctx, order_id, share=True)
    matching = (
        set(
            (
                await db.execute(
                    text("SELECT d.id" + sql + " AND d.id=ANY(:selected)"),
                    params | {"selected": [x["id"] for x in rows]},
                )
            ).scalars()
        )
        if rows
        else set()
    )
    total = (await db.execute(text("SELECT count(*)" + sql), params)).scalar_one()
    return {
        "items": [
            await document_detail(db, ctx, x["id"], False) for x in rows if x["id"] in matching
        ],
        "total": total,
        "page": page,
        "page_size": size,
    }
