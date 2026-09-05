import json
import re
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.context import RuntimeContext
from forge_erp.core.errors import Problem
from forge_erp.modules.catalog.application.rules import project_prices
from forge_erp.modules.catalog.domain.values import ConversionSnapshot


def normalized(value: str) -> str:
    return value.strip().lower().replace("×", "*").replace("＊", "*")


async def search_products(
    db: AsyncSession,
    ctx: RuntimeContext,
    q: str = "",
    page: int = 1,
    page_size: int = 25,
    active: bool | None = True,
    filters: dict | None = None,
    attributes: dict | None = None,
) -> dict:
    ctx.require("catalog.read")
    q = normalized(q)
    params = {
        "org": ctx.organization_id,
        "q": q,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    clauses = ["p.organization_id=:org"]
    if active is not None:
        clauses.append("p.active=:active")
        params["active"] = active
    for field in ("category_id", "brand_id"):
        if (filters or {}).get(field):
            clauses.append(f"p.{field}=:{field}")
            params[field] = (filters or {})[field]
    if attributes:
        clauses.append("p.attributes @> CAST(:attributes AS jsonb)")
        params["attributes"] = json.dumps(attributes)
    base_clauses = clauses.copy()
    exact = "lower(p.sku)=:q OR lower(p.barcode)=:q"
    supplier_exact = "false"
    if "supplier.read" in ctx.permissions:
        supplier_exact = """EXISTS (SELECT 1 FROM forge.supplier_products sp
            WHERE sp.organization_id=:org AND sp.product_id=p.id AND sp.active
            AND lower(sp.supplier_sku)=:q)"""
    tokens = []
    for i, token in enumerate(re.split(r"\s+", q)[:20]):
        if not token:
            continue
        params[f"token{i}"] = (
            "%" + token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        tokens.append(f"p.search_text ILIKE :token{i}")
    if q:
        clauses.append(
            f"({exact} OR {supplier_exact} OR ({' AND '.join(tokens)}) OR p.search_text % :q)"
        )
    where = " AND ".join(clauses)
    rank = f"""CASE WHEN lower(p.sku)=:q THEN 0 WHEN lower(p.barcode)=:q THEN 1
        WHEN replace(lower(p.specification),'×','*')=:q THEN 2
        WHEN {supplier_exact} THEN 3 ELSE 4 END"""
    total = (
        await db.execute(text(f"SELECT count(*) FROM forge.products p WHERE {where}"), params)
    ).scalar_one()
    if total == 0 and q:
        from forge_erp.modules.catalog.application.semantic import semantic_ids

        ids = await semantic_ids(db, ctx, q, base_clauses, params)
        if ids:
            from forge_erp.modules.catalog.infrastructure.embeddings import model_identity

            params["semantic_ids"] = [str(i) for i in ids]
            params["semantic_model"] = model_identity()
            where = " AND ".join(base_clauses) + " AND p.id=ANY(CAST(:semantic_ids AS uuid[]))"
            where += " AND p.active AND EXISTS (SELECT 1 FROM forge.product_embeddings e "
            where += "WHERE e.organization_id=p.organization_id AND e.product_id=p.id "
            where += "AND e.product_version=p.version AND e.model_identity=:semantic_model)"
            rank = "array_position(CAST(:semantic_ids AS uuid[]),p.id)"
            total = len(ids)
    rows = (
        await db.execute(
            text(
                f"SELECT p.* FROM forge.products p WHERE {where} ORDER BY {rank}, "
                "similarity(p.search_text,:q) DESC,p.sku,p.id LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings()
    items = [{k: v for k, v in r.items() if k != "organization_id"} for r in rows]
    return {
        "items": await project_prices(db, ctx, "products", items),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def conversion(
    db: AsyncSession, ctx: RuntimeContext, product_id: UUID, unit_id: UUID, qty: Decimal
) -> ConversionSnapshot:
    ctx.require("catalog.read")
    row = (
        (
            await db.execute(
                text("""SELECT pu.unit_to_base_factor,pu.version FROM forge.product_units pu
        JOIN forge.products p ON p.organization_id=pu.organization_id AND p.id=pu.product_id
        JOIN forge.units u ON u.organization_id=pu.organization_id AND u.id=pu.unit_id
        WHERE pu.organization_id=:org AND pu.product_id=:product AND pu.unit_id=:unit
        AND pu.active AND p.active AND u.active"""),
                {"org": ctx.organization_id, "product": product_id, "unit": unit_id},
            )
        )
        .mappings()
        .first()
    )
    if not row:
        raise Problem(404, "CONVERSION_NOT_FOUND", "商品或单位换算不可用")
    try:
        return ConversionSnapshot.capture(
            product_id, unit_id, qty, row["unit_to_base_factor"], row["version"]
        )
    except ValidationError as exc:
        raise Problem(422, "INVALID_QUANTITY", "数量超出范围或超过六位小数精度") from exc


async def resolve_price(
    db: AsyncSession, ctx: RuntimeContext, product_id: UUID, customer_id: UUID | None
) -> dict:
    ctx.require("catalog.read")
    ctx.require("product.price.read")
    params = {
        "org": ctx.organization_id,
        "product": product_id,
        "customer": customer_id,
        "tier": "standard",
    }
    product = (
        await db.execute(
            text(
                "SELECT base_unit_id FROM forge.products WHERE organization_id=:org "
                "AND id=:product AND active"
            ),
            params,
        )
    ).first()
    if not product:
        raise Problem(404, "NOT_FOUND", "商品不可用")
    if customer_id:
        ctx.require("customer.read")
        tier = (
            await db.execute(
                text(
                    "SELECT price_tier FROM forge.customers WHERE organization_id=:org "
                    "AND id=:customer AND active"
                ),
                params,
            )
        ).scalar_one_or_none()
        if tier is None:
            raise Problem(404, "NOT_FOUND", "客户不可用")
        params["tier"] = tier
    price = (
        (
            await db.execute(
                text("""SELECT price,price_type FROM forge.product_prices
        WHERE organization_id=:org AND product_id=:product AND active
        AND ((price_type='customer' AND customer_id=:customer)
            OR (customer_id IS NULL AND price_type IN (:tier,'standard')))
        ORDER BY CASE WHEN price_type='customer' THEN 0 WHEN price_type=:tier THEN 1 ELSE 2 END
        LIMIT 1"""),
                params,
            )
        )
        .mappings()
        .first()
    )
    return {
        "product_id": product_id,
        "base_unit_id": product[0],
        "price": price["price"] if price else None,
        "source": price["price_type"] if price else "unset",
    }
