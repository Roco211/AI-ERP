import base64
import hashlib
import hmac
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.errors import Problem
from forge_erp.modules.inventory.application.documents import lines

COST_FIELDS = {
    "inventory_value",
    "avg_unit_cost",
    "input_unit_cost",
    "value_delta",
    "before_avg_cost",
    "after_avg_cost",
    "rounding_delta",
}


def redact(row: dict, ctx: RuntimeContext) -> dict:
    return {
        k: v
        for k, v in row.items()
        if k != "organization_id"
        and (k not in COST_FIELDS or "product.cost.read" in ctx.permissions)
        and (k != "sales_order_id" or "sales.read" in ctx.permissions)
    }


def filters(ctx: RuntimeContext, warehouse: UUID | None, product: UUID | None, q: str, alias="b"):
    params: dict = {"org": ctx.organization_id}
    where = [f"{alias}.organization_id=:org"]
    if warehouse:
        where.append(f"{alias}.warehouse_id=:warehouse")
        params["warehouse"] = warehouse
    if product:
        where.append(f"{alias}.product_id=:product")
        params["product"] = product
    if q:
        where.append("(p.sku ILIKE :q OR p.name ILIKE :q)")
        params["q"] = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return params, " AND ".join(where)


async def balances(
    db: AsyncSession,
    ctx: RuntimeContext,
    warehouse: UUID | None,
    product: UUID | None,
    q: str,
    page: int,
    page_size: int,
) -> dict:
    ctx.require("inventory.read")
    params, where = filters(ctx, warehouse, product, q)
    sql = (
        "FROM forge.inventory_balances b JOIN forge.products p "
        "ON (p.organization_id,p.id)=(b.organization_id,b.product_id) "
    )
    sql += "JOIN forge.warehouses w ON (w.organization_id,w.id)=(b.organization_id,b.warehouse_id) "
    sql += (
        "JOIN forge.units u ON (u.organization_id,u.id)=(p.organization_id,p.base_unit_id) WHERE "
        + where
    )
    total = (await db.execute(text("SELECT count(*) " + sql), params)).scalar_one()
    rows = (
        await db.execute(
            text(
                "SELECT b.*,b.on_hand_qty-b.reserved_qty AS available_qty,"
                "p.name AS product_name,p.sku,w.name AS warehouse_name,u.name AS unit_name "
                + sql
                + " ORDER BY w.name,p.sku,b.id LIMIT :limit OFFSET :offset"
            ),
            params | {"limit": page_size, "offset": (page - 1) * page_size},
        )
    ).mappings()
    return {
        "items": [redact(dict(r), ctx) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def documents(
    db: AsyncSession,
    ctx: RuntimeContext,
    page: int,
    page_size: int,
    id: UUID | None = None,
    kind: str | None = None,
) -> dict:
    ctx.require("inventory.read")
    params = {"org": ctx.organization_id, "limit": page_size, "offset": (page - 1) * page_size}
    where = "d.organization_id=:org"
    if id:
        where += " AND d.id=:id"
        params["id"] = id
    if kind:
        where += " AND d.type=:kind"
        params["kind"] = kind
    sql = "FROM forge.inventory_documents d JOIN forge.warehouses w "
    sql += (
        "ON (w.organization_id,w.id)=(d.organization_id,d.warehouse_id) "
        "LEFT JOIN forge.warehouses t "
    )
    sql += "ON (t.organization_id,t.id)=(d.organization_id,d.target_warehouse_id) WHERE " + where
    total = (await db.execute(text("SELECT count(*) " + sql), params)).scalar_one()
    rows = (
        (
            await db.execute(
                text(
                    "SELECT d.*,w.name AS warehouse_name,t.name AS target_warehouse_name "
                    + sql
                    + " ORDER BY d.created_at DESC,d.id DESC LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    items = []
    for row in rows:
        item = redact(dict(row), ctx)
        if id:
            item["lines"] = [redact(x, ctx) for x in await lines(db, ctx, id)]
        items.append(item)
    if id:
        if not items:
            raise Problem(404, "NOT_FOUND", "单据不存在或无权访问")
        return items[0]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def encode_cursor(data: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(data, sort_keys=True).encode()).decode().rstrip("=")
    signature = hmac.new(
        settings().session_secret.encode(), raw.encode(), hashlib.sha256
    ).hexdigest()
    return raw + "." + signature


def decode_cursor(token: str) -> dict:
    try:
        raw, signature = token.split(".")
        expected = hmac.new(
            settings().session_secret.encode(), raw.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, TypeError, KeyError) as exc:
        raise Problem(422, "INVALID_CURSOR", "分页位置无效，请重新查询") from exc


async def movements(
    db: AsyncSession,
    ctx: RuntimeContext,
    warehouse: UUID | None,
    product: UUID | None,
    document: UUID | None,
    page_size: int,
    cursor: str | None,
) -> dict:
    ctx.require("inventory.read")
    scope = [str(ctx.organization_id), str(warehouse), str(product), str(document)]
    if cursor:
        state = decode_cursor(cursor)
        if state.get("scope") != scope:
            raise Problem(422, "INVALID_CURSOR", "筛选条件已变化，请重新查询")
    else:
        snapshot = (await db.execute(text("SELECT pg_current_snapshot()::text"))).scalar_one()
        state = {"scope": scope, "snapshot": snapshot}
    params, where = filters(ctx, warehouse, product, "", alias="m")
    where += " AND pg_visible_in_snapshot(m.created_xid,CAST(:snapshot AS pg_snapshot))"
    params["snapshot"] = state["snapshot"]
    if document:
        where += " AND m.document_id=:document"
        params["document"] = document
    if state.get("last_id"):
        where += " AND (m.created_at,m.id)<(CAST(:last_time AS timestamptz),CAST(:last_id AS uuid))"
        params |= {"last_time": state["last_time"], "last_id": state["last_id"]}
    rows = (
        (
            await db.execute(
                text(
                    "SELECT m.*,l.product_label,l.unit_label,w.name AS warehouse_name,"
                    "d.number AS document_number,d.type AS document_type,"
                    "sd.order_id AS sales_order_id FROM forge.inventory_movements m "
                    "JOIN forge.inventory_document_lines l ON "
                    "(l.organization_id,l.id)=(m.organization_id,m.line_id) "
                    "JOIN forge.inventory_documents d ON "
                    "(d.organization_id,d.id)=(m.organization_id,m.document_id) "
                    "LEFT JOIN forge.sales_documents sd ON "
                    "(sd.organization_id,sd.id)=(d.organization_id,d.id) "
                    "JOIN forge.warehouses w ON (w.organization_id,"
                    "w.id)=(m.organization_id,m.warehouse_id) "
                    "WHERE " + where + " ORDER BY m.created_at DESC,m.id DESC LIMIT :limit"
                ),
                params | {"limit": page_size + 1},
            )
        )
        .mappings()
        .all()
    )
    next_cursor = None
    if len(rows) > page_size:
        last = rows[page_size - 1]
        next_cursor = encode_cursor(
            state | {"last_time": last["created_at"].isoformat(), "last_id": str(last["id"])}
        )
    return {
        "items": [redact(dict(r), ctx) for r in rows[:page_size]],
        "next_cursor": next_cursor,
        "cutoff": encode_cursor({"scope": scope, "snapshot": state["snapshot"]}),
    }


async def low_stock(db: AsyncSession, ctx: RuntimeContext, page: int, page_size: int) -> dict:
    ctx.require("inventory.read")
    sql = """FROM forge.products p JOIN forge.units u
    ON (u.organization_id,u.id)=(p.organization_id,p.base_unit_id)
    LEFT JOIN (SELECT organization_id,product_id,sum(on_hand_qty-reserved_qty) AS available_qty
    FROM forge.inventory_balances WHERE organization_id=:org GROUP BY organization_id,product_id) b
    ON (b.organization_id,b.product_id)=(p.organization_id,p.id)
    WHERE p.organization_id=:org AND p.active AND p.min_stock_qty>0
    AND coalesce(b.available_qty,0)<=p.min_stock_qty"""
    params = {"org": ctx.organization_id, "limit": page_size, "offset": (page - 1) * page_size}
    total = (await db.execute(text("SELECT count(*) " + sql), params)).scalar_one()
    rows = (
        await db.execute(
            text(
                "SELECT p.id AS product_id,p.sku,p.name AS product_name,"
                "u.name AS unit_name,p.min_stock_qty,coalesce(b.available_qty,0) AS available_qty "
                + sql
                + " ORDER BY p.sku,p.id LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings()
    return {"items": [dict(x) for x in rows], "total": total, "page": page, "page_size": page_size}
