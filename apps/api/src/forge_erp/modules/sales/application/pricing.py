from decimal import Decimal

from sqlalchemy import text

from forge_erp.core.errors import Problem
from forge_erp.modules.inventory.domain.values import InventoryError
from forge_erp.modules.sales.domain.schemas import PriceSource
from forge_erp.modules.sales.domain.values import converted_price


async def resolve(db, ctx, customer, product, unit, factor, base_unit):
    """Called by commands after shared master locks; catalog prices remain per base unit."""
    params = {
        "org": ctx.organization_id,
        "customer": customer["id"],
        "product": product,
        "tier": customer["price_tier"],
    }
    prices = (
        (
            await db.execute(
                text("""SELECT id,price,price_type FROM forge.product_prices
      WHERE organization_id=:org AND product_id=:product AND active
      AND ((price_type='customer' AND customer_id=:customer)
       OR (customer_id IS NULL AND price_type IN (:tier,'standard')))
      ORDER BY CASE WHEN price_type='customer' THEN 0 WHEN price_type=:tier THEN 1 ELSE 2 END
      FOR SHARE"""),
                params,
            )
        )
        .mappings()
        .all()
    )
    history = (
        (
            await db.execute(
                text("""SELECT dl.id,dl.unit_price,l.unit_id,l.unit_to_base_factor
      FROM forge.sales_document_lines dl
      JOIN forge.sales_documents sd ON
(sd.organization_id,sd.id)=(dl.organization_id,dl.document_id)
      JOIN forge.inventory_documents d ON (d.organization_id,d.id)=(sd.organization_id,sd.id)
      JOIN forge.inventory_document_lines l ON (l.organization_id,l.id)=(dl.organization_id,dl.id)
      JOIN forge.sales_orders o ON (o.organization_id,o.id)=(sd.organization_id,sd.order_id)
      WHERE dl.organization_id=:org AND o.customer_id=:customer AND l.product_id=:product
       AND sd.kind='SHIPMENT' AND d.status='POSTED'
      ORDER BY d.posted_at DESC,d.id DESC,l.id DESC LIMIT 1"""),
                params,
            )
        )
        .mappings()
        .first()
    )
    source = PriceSource(source="unset", target_unit_id=unit, target_factor=factor)
    selected = prices[0] if prices else None
    if selected and selected["price_type"] == "customer":
        pass
    elif history:
        source = source.model_copy(
            update={
                "source": "history",
                "source_id": history["id"],
                "original_price": history["unit_price"],
                "original_unit_id": history["unit_id"],
                "original_factor": history["unit_to_base_factor"],
            }
        )
        selected = None
    if selected:
        source = source.model_copy(
            update={
                "source": selected["price_type"],
                "source_id": selected["id"],
                "original_price": selected["price"],
                "original_unit_id": base_unit,
                "original_factor": Decimal(1),
            }
        )
    try:
        price = (
            converted_price(source.original_price, factor, source.original_factor)
            if source.original_price is not None and source.original_factor is not None
            else None
        )
    except InventoryError as exc:
        raise Problem(409, exc.code, exc.detail) from exc
    return {
        "product_id": product,
        "customer_id": customer["id"],
        "unit_price": price,
        "price_source": source.model_dump(mode="json"),
    }


async def quote(db, ctx, customer, product, unit):
    for permission in ("sales.read", "product.price.read", "customer.read", "catalog.read"):
        ctx.require(permission)
    # Shared locks give one consistent source snapshot through completion of this query.
    from forge_erp.modules.sales.application.orders import conversion, refs

    names = await refs(db, ctx, customer, None, [product], [unit])
    pu = await conversion(db, ctx, product, unit)
    return await resolve(
        db,
        ctx,
        names[("customers", customer)],
        product,
        unit,
        pu["unit_to_base_factor"],
        names[("products", product)]["base_unit_id"],
    )
