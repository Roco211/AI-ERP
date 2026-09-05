from decimal import Decimal

from sqlalchemy import text

from forge_erp.modules.sales.application import orders

PRICE_FIELDS = {
    "unit_price",
    "amount",
    "inventory_value_delta",
    "valuation_difference",
    "input_unit_cost",
    "price_source",
    "pricing_mode",
}


def redact(ctx, row):
    return {
        k: v
        for k, v in row.items()
        if k != "organization_id"
        and (k not in PRICE_FIELDS or "product.price.read" in ctx.permissions)
    }


async def order_detail(db, ctx, id, include_lines=True):
    ctx.require("sales.read")
    row = await orders.load(db, ctx, id)
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
    total = (
        await db.execute(text("SELECT count(*) FROM forge.sales_orders WHERE " + where), params)
    ).scalar_one()
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
    return {
        "items": [await order_detail(db, ctx, id, False) for id in ids],
        "total": total,
        "page": page,
        "page_size": size,
    }
