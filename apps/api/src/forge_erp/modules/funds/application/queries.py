"""Tenant-scoped, separately authorized receivable/payable read models."""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

from forge_erp.core.config import settings as app_settings
from forge_erp.core.errors import Problem
from forge_erp.modules.funds.application import shared as f


def search(value):
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def page_result(db, sql, params, page, page_size):
    rows = (
        (
            await db.execute(
                text(
                    sql + ", filtered_page AS (SELECT * FROM filtered "
                    "ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset) "
                    "SELECT p.*,n.total FROM (SELECT count(*) total FROM filtered) n "
                    "LEFT JOIN filtered_page p ON true ORDER BY p.created_at DESC,p.id DESC"
                ),
                {**params, "limit": page_size, "offset": (page - 1) * page_size},
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [dict(row) for row in rows if row["id"] is not None],
        "total": rows[0]["total"],
        "page": page,
        "page_size": page_size,
    }


async def settings(db, ctx):
    if not ctx.permissions.intersection(
        {"funds.ar.read", "funds.ap.read", "funds.activate", "funds.opening"}
    ):
        ctx.require("funds.activate")
    row = (
        (
            await db.execute(
                text(
                    "SELECT business_date,created_at activated_at,reason "
                    "FROM forge.funds_activation WHERE organization_id=:org"
                ),
                {"org": ctx.organization_id},
            )
        )
        .mappings()
        .first()
    )
    timezone = app_settings().business_timezone
    return {
        "enabled": row is not None,
        "business_timezone": timezone,
        "business_today": datetime.now(ZoneInfo(timezone)).date(),
        **dict(row or {}),
    }


async def summary(db, ctx, side, party_id=None):
    f.require_read(ctx, side)
    row = (
        (
            await db.execute(
                text(
                    f.SOURCE_CTE + "SELECT "
                    "coalesce(sum(balance),0) balance,"
                    "coalesce(sum(settlement_amount),0) settlement_amount,"
                    "coalesce(sum(refund_amount),0) refund_amount,"
                    "coalesce(sum(amount),0) source_amount,"
                    "coalesce(sum(settled_amount),0) settled_amount,"
                    "coalesce(sum(refunded_amount),0) refunded_amount,"
                    "count(DISTINCT party_id) party_count FROM source_view WHERE side=:side "
                    + ("AND party_id=:party" if party_id else "")
                ),
                {"org": ctx.organization_id, "side": side, "party": party_id},
            )
        )
        .mappings()
        .one()
    )
    return {"side": side, **dict(row)}


async def parties(db, ctx, side, page=1, page_size=25, q=""):
    f.require_read(ctx, side)
    table = "customers" if side == "AR" else "suppliers"
    return await page_result(
        db,
        f.SOURCE_CTE + ", filtered AS ("
        "SELECT p.id,p.code,p.name,p.active is_active,p.created_at,"
        "coalesce(sum(s.balance),0) balance,coalesce(sum(s.settlement_amount),0) settlement_amount,"
        "coalesce(sum(s.refund_amount),0) refund_amount "
        f"FROM forge.{table} p LEFT JOIN source_view s ON s.party_id=p.id AND s.side=:side "
        "WHERE p.organization_id=:org AND (p.name ILIKE :q OR p.code ILIKE :q) GROUP BY p.id)",
        {"org": ctx.organization_id, "side": side, "q": search(q)},
        page,
        page_size,
    )


async def sources(db, ctx, side, page=1, page_size=25, party_id=None, status=None, q=""):
    f.require_read(ctx, side)
    return await page_result(
        db,
        f.SOURCE_CTE + ", filtered AS (SELECT * FROM source_view "
        "WHERE side=:side AND (number ILIKE :q OR party_name ILIKE :q "
        "OR coalesce(source_document_number,'') ILIKE :q) "
        + ("AND party_id=:party " if party_id else "")
        + ("AND status=:status " if status else "")
        + ")",
        {
            "org": ctx.organization_id,
            "side": side,
            "party": party_id,
            "status": status,
            "q": search(q),
        },
        page,
        page_size,
    )


ALLOCATION_SELECT = """
SELECT a.cash_id,d.number cash_number,d.kind cash_kind,
 CASE WHEN r.id IS NULL THEN 'POSTED' ELSE 'REVERSED' END cash_status,
 a.source_id,s.number source_number,a.amount
FROM forge.funds_cash_allocations a
JOIN forge.funds_cash_documents d ON d.organization_id=a.organization_id AND d.id=a.cash_id
JOIN forge.funds_sources s ON s.organization_id=a.organization_id AND s.id=a.source_id
LEFT JOIN forge.funds_cash_reversals r ON r.organization_id=d.organization_id AND r.cash_id=d.id
WHERE a.organization_id=:org
"""


async def source_detail(db, ctx, id):
    initial = await f.source(db, ctx, id)
    f.require_read(ctx, initial["side"])
    await f.lock_party(db, ctx, initial["side"], initial["party_id"], shared=True)
    row = await f.source(db, ctx, id)
    row["entries"] = [
        dict(x)
        for x in (
            await db.execute(
                text(
                    "SELECT * FROM forge.funds_entries "
                    "WHERE organization_id=:org AND source_id=:id ORDER BY created_at,id"
                ),
                {"org": ctx.organization_id, "id": id},
            )
        ).mappings()
    ]
    row["cash"] = [
        dict(x)
        for x in (
            await db.execute(
                text(ALLOCATION_SELECT + " AND a.source_id=:id ORDER BY d.created_at,d.id"),
                {"org": ctx.organization_id, "id": id},
            )
        ).mappings()
    ]
    return row


CASH_SELECT = """
SELECT d.*,CASE WHEN r.id IS NULL THEN 'POSTED' ELSE 'REVERSED' END status,
 r.created_at reversed_at,r.reason reversal_reason
FROM forge.funds_cash_documents d LEFT JOIN forge.funds_cash_reversals r
 ON r.organization_id=d.organization_id AND r.cash_id=d.id WHERE d.organization_id=:org
"""


async def raw_cash(db, ctx, id):
    row = (
        (
            await db.execute(
                text(CASH_SELECT + " AND d.id=:id"), {"org": ctx.organization_id, "id": id}
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "NOT_FOUND", "收付款记录不存在或无权访问")
    return dict(row)


async def cash_list(db, ctx, side, page=1, page_size=25, party_id=None, kind=None):
    f.require_read(ctx, side)
    return await page_result(
        db,
        "WITH filtered AS ("
        + CASH_SELECT
        + " AND d.side=:side "
        + ("AND d.party_id=:party " if party_id else "")
        + ("AND d.kind=:kind " if kind else "")
        + ")",
        {"org": ctx.organization_id, "side": side, "party": party_id, "kind": kind},
        page,
        page_size,
    )


async def cash_detail(db, ctx, id):
    row = await raw_cash(db, ctx, id)
    f.require_read(ctx, row["side"])
    await f.lock_party(db, ctx, row["side"], row["party_id"], shared=True)
    row = await raw_cash(db, ctx, id)
    row["allocations"] = [
        dict(x)
        for x in (
            await db.execute(
                text(ALLOCATION_SELECT + " AND a.cash_id=:id ORDER BY a.id"),
                {"org": ctx.organization_id, "id": id},
            )
        ).mappings()
    ]
    return row


def commercial_sql(side):
    prefix, party, kind = (
        ("sales", "customer", "SHIPMENT") if side == "AR" else ("purchase", "supplier", "RECEIPT")
    )
    return f"""
SELECT d.id,d.number,d.status,d.posted_at,d.created_at,x.order_id,x.kind,x.original_document_id,
 o.{party}_id party_id,o.{party}_name party_name,
 coalesce((SELECT sum(l.amount) FROM forge.{prefix}_document_lines l
  WHERE l.organization_id=x.organization_id AND l.document_id=x.id),0) commercial_amount,
 coalesce((SELECT sum(l.amount) FROM forge.{prefix}_documents r
  JOIN forge.inventory_documents rd ON rd.organization_id=r.organization_id AND rd.id=r.id
  JOIN forge.{prefix}_document_lines l ON l.organization_id=r.organization_id AND l.document_id=r.id
  WHERE r.organization_id=x.organization_id AND r.original_document_id=x.id
   AND rd.status='POSTED'),0) returned_amount
FROM forge.{prefix}_documents x
JOIN forge.inventory_documents d ON d.organization_id=x.organization_id AND d.id=x.id
JOIN forge.{prefix}_orders o ON o.organization_id=x.organization_id AND o.id=x.order_id
WHERE x.organization_id=:org
"""


async def commercial_document(db, ctx, side, id, *, lock=False):
    row = (
        (
            await db.execute(
                text(commercial_sql(side) + " AND x.id=:id" + (" FOR SHARE OF d" if lock else "")),
                {"org": ctx.organization_id, "id": id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "NOT_FOUND", "商业来源单据不存在或无权访问")
    return dict(row)


async def legacy_documents(db, ctx, side, page=1, page_size=25, party_id=None):
    ctx.require("funds.opening")
    f.require_read(ctx, side)
    await f.cutover(db, ctx, required=True)
    kind = "SHIPMENT" if side == "AR" else "RECEIPT"
    return await page_result(
        db,
        "WITH commercial AS (" + commercial_sql(side) + "), filtered AS ("
        "SELECT c.*,:side side,commercial_amount-returned_amount effective_amount "
        "FROM commercial c WHERE c.kind=:kind AND c.status='POSTED' "
        "AND NOT EXISTS(SELECT 1 FROM forge.funds_sources f "
        "WHERE f.organization_id=:org AND f.source_document_id=c.id) "
        + ("AND party_id=:party " if party_id else "")
        + ")",
        {"org": ctx.organization_id, "side": side, "kind": kind, "party": party_id},
        page,
        page_size,
    )


async def order_summary(db, ctx, side, order_id):
    f.require_read(ctx, side)
    prefix, kind = ("sales", "SHIPMENT") if side == "AR" else ("purchase", "RECEIPT")
    # The parent query already holds its order lock. A single statement reads all
    # money and integration flags, without obtaining locks in the opposite order.
    row = dict(
        (
            await db.execute(
                text(
                    f.SOURCE_CTE
                    + f"""
SELECT EXISTS(SELECT 1 FROM forge.funds_activation WHERE organization_id=:org) enabled,
 (SELECT count(*) FROM forge.{prefix}_documents x JOIN forge.inventory_documents d
   ON d.organization_id=x.organization_id AND d.id=x.id
  WHERE x.organization_id=:org AND x.order_id=:order AND x.kind=:kind AND d.status='POSTED'
  AND NOT EXISTS(SELECT 1 FROM source_view v WHERE v.source_document_id=x.id))
  unmapped_document_count,
 count(*) source_count,coalesce(sum(amount),0) source_amount,
 coalesce(sum(historically_settled_amount),0) historically_settled_amount,
 coalesce(sum(settled_amount),0) settled_amount,coalesce(sum(refunded_amount),0) refunded_amount,
 coalesce(sum(balance),0) balance,coalesce(sum(settlement_amount),0) settlement_amount,
 coalesce(sum(refund_amount),0) refund_amount
FROM source_view s WHERE s.side=:side AND s.source_document_id IN
 (SELECT id FROM forge.{prefix}_documents WHERE organization_id=:org
  AND order_id=:order AND kind=:kind)
"""
                ),
                {"org": ctx.organization_id, "side": side, "order": order_id, "kind": kind},
            )
        )
        .mappings()
        .one()
    )
    row["integration_status"] = (
        "NOT_ENABLED"
        if not row["enabled"]
        else "INCOMPLETE"
        if row["unmapped_document_count"]
        else "ACTIVE"
    )
    row["settlement_status"] = None
    if row["integration_status"] == "ACTIVE" and row["source_count"]:
        row["settlement_status"] = (
            "PAID"
            if row["settlement_amount"] == 0
            else "PARTIAL"
            if row["historically_settled_amount"] + row["settled_amount"] > row["refunded_amount"]
            else "UNPAID"
        )
    return row
