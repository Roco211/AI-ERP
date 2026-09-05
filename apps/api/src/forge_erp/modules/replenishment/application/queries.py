from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from forge_erp.core.config import settings
from forge_erp.core.errors import Problem
from forge_erp.core.security import fingerprint
from forge_erp.modules.inventory.domain.values import InventoryError
from forge_erp.modules.replenishment.domain.schemas import (
    Suggestion,
    SuggestionCounts,
    SuggestionsPage,
    Window,
)
from forge_erp.modules.replenishment.domain.values import ALGORITHM_VERSION, calculate, sales_window

READ_PERMISSIONS = (
    "replenishment.read",
    "catalog.read",
    "inventory.read",
    "sales.read",
    "purchase.read",
    "supplier.read",
)
BASIS_FIELDS = (
    "product_id",
    "sku",
    "name",
    "product_version",
    "category_id",
    "base_unit_id",
    "base_unit_name",
    "base_unit_version",
    "base_unit_active",
    "default_purchase_unit_id",
    "preferred_supplier_id",
    "preferred_supplier_name",
    "supplier_version",
    "supplier_active",
    "supplier_product_id",
    "supplier_product_version",
    "lead_days",
    "available_qty",
    "open_purchase_qty",
    "shipped_qty",
    "returned_qty",
    "safety_stock_qty",
    "minimum_reorder_qty",
)


def require_read(ctx):
    for permission in READ_PERMISSIONS:
        ctx.require(permission)


def require_create(ctx):
    require_read(ctx)
    for permission in ("replenishment.create", "purchase.order.write", "product.cost.read"):
        ctx.require(permission)


def window(as_of: datetime | None = None) -> Window:
    as_of = as_of or datetime.now(UTC)
    timezone = settings().business_timezone
    start, end = sales_window(as_of, timezone)
    return Window(
        as_of=as_of,
        business_timezone=timezone,
        window_start=start,
        window_end=end,
        algorithm_version=ALGORITHM_VERSION,
    )


# Each fact family is aggregated independently before joining products. Returns never
# reopen purchase quantities; reservations never become sales. Comparisons use exact
# numerators in thirtieths, so SQL filters and the domain calculation agree at boundaries.
BASIS_CTE = """
WITH selected AS MATERIALIZED (
 SELECT p.* FROM forge.products p WHERE p.organization_id=:org AND p.active
 AND (CAST(:ids AS uuid[]) IS NULL OR p.id=ANY(CAST(:ids AS uuid[])))
 AND (CAST(:category AS uuid) IS NULL OR p.category_id=:category)
 AND (CAST(:supplier AS uuid) IS NULL OR p.preferred_supplier_id=:supplier)
 AND (:q='' OR p.sku ILIKE :search OR p.name ILIKE :search)
), balances AS (
 SELECT b.product_id,sum(b.on_hand_qty-b.reserved_qty) available
 FROM forge.inventory_balances b JOIN selected p ON p.id=b.product_id
 WHERE b.organization_id=:org GROUP BY b.product_id
), received AS (
 SELECT pl.order_line_id,sum(il.base_qty) qty
 FROM forge.purchase_document_lines pl
 JOIN forge.purchase_documents pd ON (pd.organization_id,pd.id)=(pl.organization_id,pl.document_id)
 JOIN forge.inventory_documents d ON (d.organization_id,d.id)=(pd.organization_id,pd.id)
 JOIN forge.inventory_document_lines il ON (il.organization_id,il.id)=(pl.organization_id,pl.id)
 JOIN selected p ON p.id=il.product_id
 WHERE pl.organization_id=:org AND pd.kind='RECEIPT'
 AND d.type='PURCHASE_RECEIPT' AND d.status='POSTED' GROUP BY pl.order_line_id
), inbound AS (
 SELECT l.product_id,sum(greatest(0,l.base_qty-coalesce(r.qty,0))) qty
 FROM forge.purchase_order_lines l
 JOIN forge.purchase_orders o ON (o.organization_id,o.id)=(l.organization_id,l.order_id)
 JOIN selected p ON p.id=l.product_id LEFT JOIN received r ON r.order_line_id=l.id
 WHERE l.organization_id=:org AND o.status='CONFIRMED' GROUP BY l.product_id
), sales AS (
 SELECT il.product_id,
 coalesce(sum(il.base_qty) FILTER(WHERE sd.kind='SHIPMENT'),0) shipped,
 coalesce(sum(il.base_qty) FILTER(WHERE sd.kind='RETURN'),0) returned
 FROM forge.sales_documents sd
 JOIN forge.inventory_documents d ON (d.organization_id,d.id)=(sd.organization_id,sd.id)
 JOIN forge.inventory_document_lines il
 ON (il.organization_id,il.document_id)=(sd.organization_id,sd.id)
 JOIN selected p ON p.id=il.product_id
 WHERE sd.organization_id=:org AND d.status='POSTED'
 AND ((sd.kind='SHIPMENT' AND d.type='SALES_SHIPMENT')
   OR (sd.kind='RETURN' AND d.type='SALES_RETURN'))
 AND d.posted_at>=:start AND d.posted_at<:end GROUP BY il.product_id
), raw AS (
 SELECT p.id product_id,p.sku,p.name,p.version product_version,p.category_id,
 p.base_unit_id,u.name base_unit_name,u.version base_unit_version,u.active base_unit_active,
 p.default_purchase_unit_id,p.preferred_supplier_id,s.name preferred_supplier_name,
 s.version supplier_version,s.active supplier_active,sp.id supplier_product_id,
 sp.version supplier_product_version,
 CASE WHEN s.active AND sp.active THEN sp.lead_days ELSE NULL END lead_days,
 coalesce(b.available,0) available_qty,coalesce(i.qty,0) open_purchase_qty,
 coalesce(sa.shipped,0) shipped_qty,coalesce(sa.returned,0) returned_qty,
 p.min_stock_qty safety_stock_qty,p.reorder_qty minimum_reorder_qty
 FROM selected p JOIN forge.units u ON (u.organization_id,u.id)=(p.organization_id,p.base_unit_id)
 LEFT JOIN forge.suppliers s ON (s.organization_id,s.id)=(p.organization_id,p.preferred_supplier_id)
 LEFT JOIN forge.supplier_products sp ON sp.organization_id=p.organization_id
   AND sp.product_id=p.id AND sp.supplier_id=p.preferred_supplier_id
 LEFT JOIN balances b ON b.product_id=p.id LEFT JOIN inbound i ON i.product_id=p.id
 LEFT JOIN sales sa ON sa.product_id=p.id
), numerator AS (
 SELECT raw.*,safety_stock_qty*30+CASE WHEN lead_days IS NOT NULL
 THEN greatest(shipped_qty-returned_qty,0)*lead_days ELSE 0 END point_n,
 safety_stock_qty*30+CASE WHEN lead_days IS NOT NULL
 THEN greatest(shipped_qty-returned_qty,0)*(lead_days+7) ELSE 0 END target_n
 FROM raw
), calculated AS (
 SELECT numerator.*,available_qty*30<=point_n candidate,
 CASE WHEN available_qty*30<=point_n
 AND target_n>(available_qty+open_purchase_qty)*30
 THEN ceil(greatest(minimum_reorder_qty*30,
 target_n-(available_qty+open_purchase_qty)*30)*1000000/30)/1000000
 ELSE 0 END suggested_base_qty FROM numerator
), filtered AS (
 SELECT * FROM calculated WHERE (NOT :candidate_only OR candidate)
 AND (NOT :suggested_only OR suggested_base_qty>0)
)
"""


def params(
    ctx,
    info,
    *,
    ids=None,
    category_id=None,
    supplier_id=None,
    q="",
    candidate_only=False,
    suggested_only=False,
):
    return {
        "org": ctx.organization_id,
        "ids": ids,
        "category": category_id,
        "supplier": supplier_id,
        "q": q,
        "search": "%" + q + "%",
        "start": info.window_start,
        "end": info.window_end,
        "candidate_only": candidate_only,
        "suggested_only": suggested_only,
    }


def suggestion(row, info: Window) -> Suggestion:
    raw = dict(row)
    # Include reference versions and the raw facts; exclude query clock and derived
    # display rounding. A new business day changes the window and requires review.
    basis = {k: raw[k] for k in BASIS_FIELDS}
    basis |= {
        "algorithm_version": info.algorithm_version,
        "window_start": info.window_start,
        "window_end": info.window_end,
        "business_timezone": info.business_timezone,
    }
    try:
        values = calculate(
            raw["available_qty"],
            raw["open_purchase_qty"],
            raw["shipped_qty"],
            raw["returned_qty"],
            raw["safety_stock_qty"],
            raw["minimum_reorder_qty"],
            raw["lead_days"],
        )
    except InventoryError as exc:
        raise Problem(422, exc.code, exc.detail) from exc
    return Suggestion.model_validate(raw | values | {"basis_hash": fingerprint(basis)})


async def selected_suggestions(db, ctx, info: Window, ids: list[UUID]):
    rows = (
        (
            await db.execute(
                text(BASIS_CTE + "SELECT * FROM filtered ORDER BY product_id"),
                params(ctx, info, ids=ids),
            )
        )
        .mappings()
        .all()
    )
    if len(rows) != len(ids):
        raise Problem(409, "INVALID_REFERENCE", "所选商品不存在、已停用或无权访问，请重新选择")
    return {r["product_id"]: (suggestion(r, info), dict(r)) for r in rows}


async def suggestions(
    db,
    ctx,
    page=1,
    page_size=25,
    category_id=None,
    supplier_id=None,
    q="",
    candidate_only=False,
    suggested_only=False,
    *,
    as_of=None,
):
    require_read(ctx)
    info = window(as_of)
    rows = (
        (
            await db.execute(
                text(
                    BASIS_CTE
                    + """
 SELECT paged.*,totals.total FROM (SELECT count(*) total FROM filtered) totals
 LEFT JOIN (SELECT * FROM filtered ORDER BY sku,product_id LIMIT :limit OFFSET :offset) paged
 ON true ORDER BY paged.sku,paged.product_id
 """
                ),
                params(
                    ctx,
                    info,
                    category_id=category_id,
                    supplier_id=supplier_id,
                    q=q,
                    candidate_only=candidate_only,
                    suggested_only=suggested_only,
                )
                | {"limit": page_size, "offset": (page - 1) * page_size},
            )
        )
        .mappings()
        .all()
    )
    return SuggestionsPage(
        **info.model_dump(),
        total=rows[0]["total"],
        page=page,
        page_size=page_size,
        items=[suggestion(r, info) for r in rows if r["product_id"] is not None],
    )


async def count_suggestions(db, ctx, as_of: datetime) -> SuggestionCounts:
    require_read(ctx)
    row = (
        (
            await db.execute(
                text(
                    BASIS_CTE
                    + """
 SELECT count(*) FILTER(WHERE candidate) candidate_count,
 count(*) FILTER(WHERE suggested_base_qty>0) suggested_count,
 bool_or(suggested_base_qty>=100000000000000) overflow FROM calculated
 """
                ),
                params(ctx, window(as_of)),
            )
        )
        .mappings()
        .one()
    )
    if row["overflow"]:
        raise Problem(422, "NUMERIC_OVERFLOW", "补货建议超出可保存范围，请核对数据")
    return SuggestionCounts.model_validate(dict(row))


async def report_sources(db, ctx, as_of, page, page_size):
    result = await suggestions(db, ctx, page, page_size, candidate_only=True, as_of=as_of)
    return {
        "items": [
            {
                "id": x.product_id,
                "product_id": x.product_id,
                "number": x.sku,
                "kind": "REPLENISHMENT",
                "label": x.name,
                "unit_name": x.base_unit_name,
                "available_qty": x.available_qty,
                "min_stock_qty": x.safety_stock_qty,
                "suggested_base_qty": x.suggested_base_qty,
            }
            for x in result.items
        ],
        "total": result.total,
        "page": page,
        "page_size": page_size,
    }


async def warehouses(db, ctx, page, page_size, q):
    require_create(ctx)
    data = {
        "org": ctx.organization_id,
        "q": "%" + q + "%",
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    rows = (
        (
            await db.execute(
                text("""
 WITH options AS (SELECT id,code,name FROM forge.warehouses WHERE organization_id=:org
 AND active AND (code ILIKE :q OR name ILIKE :q))
 SELECT p.*,t.total FROM (SELECT count(*) total FROM options) t
 LEFT JOIN (SELECT * FROM options ORDER BY code,id LIMIT :limit OFFSET :offset) p ON true
 ORDER BY p.code,p.id
 """),
                data,
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [
            {"id": r["id"], "code": r["code"], "name": r["name"]}
            for r in rows
            if r["id"] is not None
        ],
        "total": rows[0]["total"],
        "page": page,
        "page_size": page_size,
    }
