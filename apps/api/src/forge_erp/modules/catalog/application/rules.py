from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.context import RuntimeContext
from forge_erp.core.errors import Problem
from forge_erp.modules.catalog.domain.values import validate_attributes


async def rules(
    db: AsyncSession,
    ctx: RuntimeContext,
    resource: str,
    values: dict,
    record_id: UUID | None,
    previous: dict | None,
) -> None:
    params = {"org": ctx.organization_id}
    if resource == "categories":
        rows = (
            await db.execute(
                text(
                    "SELECT attributes FROM forge.products WHERE "
                    "organization_id=:org AND category_id=:id"
                ),
                params | {"id": record_id},
            )
        ).scalars()
        for attributes in rows:
            validate_attributes(values["attribute_schema"], attributes)
    if resource == "products":
        if previous and previous["base_unit_id"] != values["base_unit_id"]:
            raise Problem(409, "BASE_UNIT_IMMUTABLE", "基础单位不能修改，请新建商品")
        values["barcode"] = values.get("barcode") or None
        for key in ("default_purchase_unit_id", "default_sales_unit_id"):
            values[key] = values.get(key) or values["base_unit_id"]
            if values[key] != values["base_unit_id"]:
                await require_conversion(db, ctx, record_id, values[key])
        schema = (
            await db.execute(
                text(
                    "SELECT attribute_schema FROM forge.categories "
                    "WHERE organization_id=:org AND id=:id"
                ),
                params | {"id": values["category_id"]},
            )
        ).scalar_one()
        validate_attributes(schema, values["attributes"])
    if resource in ("product-units", "product-prices", "supplier-products"):
        if previous:
            fixed = {"product_id", "unit_id", "supplier_id", "price_type", "customer_id"}
            if any(previous.get(k) != values.get(k) for k in fixed if k in values):
                raise Problem(409, "ASSOCIATION_IMMUTABLE", "关联对象不能修改，请新建关联资料")
        if resource == "product-units":
            base = (
                await db.execute(
                    text(
                        "SELECT base_unit_id FROM forge.products "
                        "WHERE organization_id=:org AND id=:id"
                    ),
                    params | {"id": values["product_id"]},
                )
            ).scalar_one()
            if base == values["unit_id"] and values["unit_to_base_factor"] != 1:
                raise Problem(409, "BASE_FACTOR_ONE", "基础单位换算率必须为 1")
        if resource == "supplier-products":
            await require_conversion(db, ctx, values["product_id"], values["purchase_unit_id"])


async def require_conversion(db: AsyncSession, ctx: RuntimeContext, product_id, unit_id) -> None:
    row = (
        await db.execute(
            text(
                "SELECT id FROM forge.product_units "
                "WHERE organization_id=:org "
                "AND product_id=:product "
                "AND unit_id=:unit AND active"
            ),
            {"org": ctx.organization_id, "product": product_id, "unit": unit_id},
        )
    ).first()
    if row is None:
        raise Problem(409, "MISSING_UNIT_CONVERSION", "请先为该商品配置有效的单位换算")


async def check_deactivation(
    db: AsyncSession, ctx: RuntimeContext, resource: str, previous: dict
) -> None:
    if resource in ("products", "warehouses"):
        column = "product_id" if resource == "products" else "warehouse_id"
        used_stock = (
            await db.execute(
                text(
                    f"SELECT id FROM forge.inventory_balances WHERE "
                    f"organization_id=:org AND {column}=:id "
                    "AND (on_hand_qty>0 OR reserved_qty>0) LIMIT 1"
                ),
                {"org": ctx.organization_id, "id": previous["id"]},
            )
        ).first()
        if used_stock:
            raise Problem(409, "STOCK_IN_USE", "商品或仓库仍有库存或占用，不能停用")
    if resource != "product-units":
        return
    params = {
        "org": ctx.organization_id,
        "product": previous["product_id"],
        "unit": previous["unit_id"],
    }
    used = (
        await db.execute(
            text(
                "SELECT id FROM forge.products WHERE organization_id=:org "
                "AND id=:product AND :unit IN "
                "(base_unit_id,default_purchase_unit_id,default_sales_unit_id) "
                "UNION ALL SELECT id FROM forge.supplier_products WHERE organization_id=:org "
                "AND product_id=:product AND purchase_unit_id=:unit AND active"
            ),
            params,
        )
    ).first()
    if used:
        raise Problem(409, "UNIT_IN_USE", "基础单位或正在使用的默认单位不能停用")


def search_text(values: dict) -> str:
    fields = [
        values.get(k, "") or ""
        for k in ("sku", "barcode", "name", "short_name", "model", "specification")
    ]
    fields += [str(v) for v in values.get("attributes", {}).values()]
    return " ".join(fields).lower().replace("×", "*").replace("＊", "*")


async def project_prices(
    db: AsyncSession, ctx: RuntimeContext, resource: str, rows: list[dict]
) -> list[dict]:
    if resource != "products" or "product.price.read" not in ctx.permissions or not rows:
        return rows
    prices = (
        await db.execute(
            text(
                "SELECT product_id,price_type,price FROM forge.product_prices "
                "WHERE organization_id=:org AND active AND customer_id IS NULL "
                "AND product_id=ANY(CAST(:ids AS uuid[]))"
            ),
            {"org": ctx.organization_id, "ids": [str(r["id"]) for r in rows]},
        )
    ).mappings()
    by_id = {str(r["id"]): r for r in rows}
    for price in prices:
        by_id[str(price["product_id"])][price["price_type"] + "_price"] = str(price["price"])
    return rows
