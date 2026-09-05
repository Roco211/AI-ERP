from decimal import Decimal, localcontext

from sqlalchemy import text

from forge_erp.core.errors import Problem
from forge_erp.modules.sales.application import orders

PRICE_FIELDS = {
    "unit_price",
    "amount",
    "price_source",
    "pricing_mode",
    "returned_amount",
    "shipment_amount",
    "return_amount",
    "net_sales_amount",
}


COST_FIELDS = {
    "actual_cost",
    "inventory_value_delta",
    "input_unit_cost",
    "gross_margin",
    "return_cost",
    "returned_cost",
    "shipment_cost",
    "net_cost",
}

# A receipt/issue fact is the authority for cost, even when a document has a frozen draft value.
ACTUAL_COST = """(SELECT CASE WHEN m.kind='ISSUE' THEN -m.value_delta ELSE m.value_delta END
    FROM forge.inventory_movements m
    WHERE m.organization_id=l.organization_id AND m.line_id=l.id
      AND m.document_id=l.document_id AND m.operation_id=l.document_id
      AND ((d.type='SALES_SHIPMENT' AND m.kind='ISSUE')
        OR (d.type='SALES_RETURN' AND m.kind='RECEIVE')))"""

RETURN_TOTALS = """ LEFT JOIN LATERAL (
    SELECT coalesce(sum(rl.qty),0) AS returned_qty,
      coalesce(sum(rl.base_qty),0) AS returned_base_qty,
      coalesce(sum(rsl.amount),0) AS returned_amount,
      coalesce(sum(rm.value_delta),0) AS returned_cost
    FROM forge.sales_document_lines rsl
    JOIN forge.inventory_document_lines rl
      ON (rl.organization_id,rl.id)=(rsl.organization_id,rsl.id)
    JOIN forge.sales_documents rsd
      ON (rsd.organization_id,rsd.id)=(rsl.organization_id,rsl.document_id)
    JOIN forge.inventory_documents rd
      ON (rd.organization_id,rd.id)=(rsd.organization_id,rsd.id)
    LEFT JOIN forge.inventory_movements rm
      ON rm.organization_id=rl.organization_id AND rm.line_id=rl.id
        AND rm.document_id=rl.document_id AND rm.operation_id=rl.document_id
        AND rm.kind='RECEIVE'
    WHERE rsl.organization_id=sl.organization_id AND rsl.shipment_line_id=sl.id
      AND rsd.kind='RETURN' AND rd.type='SALES_RETURN' AND rd.status='POSTED'
    ) returned ON true """

PUBLIC_DOCUMENTS = """((sd.kind='SHIPMENT' AND d.type='SALES_SHIPMENT')
    OR (sd.kind='RETURN' AND d.type='SALES_RETURN'))"""


async def order_margin(db, ctx, id):
    if not {"sales.read", "product.price.read"} <= ctx.permissions:
        return {}
    facts = (
        (
            await db.execute(
                text(
                    """SELECT sd.kind,sl.amount,"""
                    + ACTUAL_COST
                    + """ AS actual_cost
            FROM forge.sales_documents sd JOIN forge.inventory_documents d
              ON (d.organization_id,d.id)=(sd.organization_id,sd.id)
            JOIN forge.sales_document_lines sl
              ON (sl.organization_id,sl.document_id)=(sd.organization_id,sd.id)
            JOIN forge.inventory_document_lines l
              ON (l.organization_id,l.id)=(sl.organization_id,sl.id)
            WHERE sd.organization_id=:org AND sd.order_id=:id AND d.status='POSTED'
              AND """
                    + PUBLIC_DOCUMENTS
                ),
                {"org": ctx.organization_id, "id": id},
            )
        )
        .mappings()
        .all()
    )
    with localcontext() as dec:
        dec.prec = 50
        zero = Decimal("0.0000")
        result = {
            "shipment_amount": sum((x["amount"] for x in facts if x["kind"] == "SHIPMENT"), zero),
            "return_amount": sum((x["amount"] for x in facts if x["kind"] == "RETURN"), zero),
        }
        result["net_sales_amount"] = result["shipment_amount"] - result["return_amount"]
        # Never silently turn a missing historical stock fact into a zero cost/margin.
        if "product.cost.read" in ctx.permissions and all(
            x["actual_cost"] is not None for x in facts
        ):
            result["shipment_cost"] = sum(
                (x["actual_cost"] for x in facts if x["kind"] == "SHIPMENT"), zero
            )
            result["return_cost"] = sum(
                (x["actual_cost"] for x in facts if x["kind"] == "RETURN"), zero
            )
            result["net_cost"] = result["shipment_cost"] - result["return_cost"]
            result["gross_margin"] = result["net_sales_amount"] - result["net_cost"]
        return result


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
    row.update(await order_margin(db, ctx, id))
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
                text(
                    """SELECT d.*,sd.order_id,sd.kind,sd.original_document_id,
        o.number AS order_number,o.customer_id,o.customer_name,o.warehouse_name
        FROM forge.sales_documents sd JOIN forge.inventory_documents d
          ON (d.organization_id,d.id)=(sd.organization_id,sd.id)
        JOIN forge.sales_orders o ON (o.organization_id,o.id)=(sd.organization_id,sd.order_id)
        WHERE sd.organization_id=:org AND sd.id=:id AND """
                    + PUBLIC_DOCUMENTS
                ),
                {"org": ctx.organization_id, "id": id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "NOT_FOUND", "销售单据不存在或无权访问")
    return dict(row)


async def raw_document_lines(db, ctx, id):
    return [
        dict(row)
        for row in (
            await db.execute(
                text(
                    """SELECT l.*,sl.order_id,sl.order_line_id,sl.unit_price,sl.amount,
        sl.shipment_line_id,sl.return_cost,"""
                    + ACTUAL_COST
                    + """ AS actual_cost,
        returned.returned_qty,returned.returned_base_qty,
        returned.returned_amount,returned.returned_cost
        FROM forge.sales_document_lines sl JOIN forge.inventory_document_lines l
          ON (l.organization_id,l.id)=(sl.organization_id,sl.id)
        JOIN forge.inventory_documents d
          ON (d.organization_id,d.id)=(l.organization_id,l.document_id)
        """
                    + RETURN_TOTALS
                    + """
        WHERE sl.organization_id=:org AND sl.document_id=:id ORDER BY l.line_no"""
                ),
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
        for line in lines:
            can_return = row["kind"] == "SHIPMENT" and row["status"] == "POSTED"
            line["returnable_qty"] = (
                line["qty"] - line["returned_qty"] if can_return else Decimal(0)
            )
            line["returnable_base_qty"] = (
                line["base_qty"] - line["returned_base_qty"] if can_return else Decimal(0)
            )
            if row["status"] == "POSTED" and line["actual_cost"] is not None:
                contribution = line["amount"] - line["actual_cost"]
                line["gross_margin"] = contribution if row["kind"] == "SHIPMENT" else -contribution
        row["amount"] = sum((x["amount"] for x in lines), Decimal("0.0000"))
        row["actual_cost"] = (
            sum((x["actual_cost"] for x in lines), Decimal("0.0000"))
            if row["status"] != "DRAFT"
            and lines
            and all(x["actual_cost"] is not None for x in lines)
            else None
        )
        if row["status"] == "POSTED" and row["actual_cost"] is not None:
            contribution = row["amount"] - row["actual_cost"]
            row["gross_margin"] = contribution if row["kind"] == "SHIPMENT" else -contribution
    row["lines"] = [redact(ctx, x) for x in lines] if include_lines else []
    return redact(ctx, row)


async def document_list(
    db, ctx, page, size, order=None, customer=None, status=None, q="", kind=None
):
    ctx.require("sales.read")
    params = {"org": ctx.organization_id, "limit": size, "offset": (page - 1) * size}
    where = "sd.organization_id=:org AND " + PUBLIC_DOCUMENTS
    for field, value, column in [
        ("order", order, "sd.order_id"),
        ("customer", customer, "o.customer_id"),
        ("status", status, "d.status"),
        ("kind", kind, "sd.kind"),
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


async def price_history(db, ctx, page, size, customer=None, product=None):
    # Transaction snapshots can be read without granting access to current master data.
    ctx.require("sales.read")
    ctx.require("product.price.read")
    where = """sd.organization_id=:org AND sd.kind='SHIPMENT'
        AND d.type='SALES_SHIPMENT' AND d.status='POSTED'"""
    params = {"org": ctx.organization_id, "limit": size, "offset": (page - 1) * size}
    for field, value, column in [
        ("customer", customer, "o.customer_id"),
        ("product", product, "l.product_id"),
    ]:
        if value is not None:
            where += f" AND {column}=:{field}"
            params[field] = value
    joins = """ FROM forge.sales_documents sd JOIN forge.inventory_documents d
      ON (d.organization_id,d.id)=(sd.organization_id,sd.id)
    JOIN forge.sales_orders o ON (o.organization_id,o.id)=(sd.organization_id,sd.order_id)
    JOIN forge.sales_document_lines sl
      ON (sl.organization_id,sl.document_id)=(sd.organization_id,sd.id)
    JOIN forge.inventory_document_lines l
      ON (l.organization_id,l.id)=(sl.organization_id,sl.id) """
    ordering = " ORDER BY d.posted_at DESC,d.id DESC,l.id DESC"
    selected = (
        (
            await db.execute(
                text(
                    "SELECT l.id,sd.order_id"
                    + joins
                    + " WHERE "
                    + where
                    + ordering
                    + " LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    for order_id in sorted({x["order_id"] for x in selected}):
        await orders.load(db, ctx, order_id, share=True)
    # Revalidate after a reversal commits during lock acquisition, as document lists do.
    items = (
        [
            dict(x)
            for x in (
                await db.execute(
                    text(
                        """SELECT l.id AS line_id,d.id AS document_id,d.number AS document_number,
                sd.order_id,o.customer_id,o.customer_name,l.product_id,l.product_label,
                l.unit_id,l.unit_label,sl.unit_price,l.qty,l.base_qty,l.unit_to_base_factor,
                l.conversion_version,sl.amount,d.posted_at,returned.returned_qty,
                returned.returned_base_qty,returned.returned_amount"""
                        + joins
                        + RETURN_TOTALS
                        + " WHERE "
                        + where
                        + " AND l.id=ANY(:selected)"
                        + ordering
                    ),
                    params | {"selected": [x["id"] for x in selected]},
                )
            ).mappings()
        ]
        if selected
        else []
    )
    total = (
        await db.execute(text("SELECT count(*)" + joins + " WHERE " + where), params)
    ).scalar_one()
    return {"items": items, "total": total, "page": page, "page_size": size}
