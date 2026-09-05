"""Bounded operating read models over existing, tenant-scoped ERP facts."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from sqlalchemy import text

from forge_erp.core.config import settings
from forge_erp.core.errors import Problem
from forge_erp.modules.funds.application.shared import SOURCE_CTE
from forge_erp.modules.sales.application.queries import ACTUAL_COST, PUBLIC_DOCUMENTS

REPL_PERMISSIONS = frozenset(
    {
        "replenishment.read",
        "catalog.read",
        "inventory.read",
        "sales.read",
        "purchase.read",
        "supplier.read",
    }
)
NOTICE = (
    "期间指标按当前有效已过账事实统计；后续合法冲销会重算历史期间。"
    "当前余额按本次查询时点展示，不代表所选日期的历史余额。销售毛利不等于会计利润。"
)
ZERO = Decimal("0.0000")


def report_scope(date_from: date | None, date_to: date | None, *, as_of=None):
    as_of = as_of or datetime.now(UTC)
    zone = ZoneInfo(settings().business_timezone)
    today = as_of.astimezone(zone).date()
    end = date_to or today
    start = date_from or date.fromordinal(max(1, end.toordinal() - 29))
    if end < start or (end - start).days >= 366 or end == date.max:
        raise Problem(422, "INVALID_REPORT_RANGE", "日期范围须按先后顺序选择，最多366日")
    return {
        "as_of": as_of,
        "business_today": today,
        "business_timezone": zone.key,
        "date_from": start,
        "date_to": end,
        "restatement_notice": NOTICE,
    }


def scope_params(ctx, scope):
    zone = ZoneInfo(scope["business_timezone"])
    try:
        start = datetime.combine(scope["date_from"], time.min, zone).astimezone(UTC)
        end = datetime.combine(scope["date_to"] + timedelta(days=1), time.min, zone).astimezone(UTC)
    except OverflowError as exc:
        raise Problem(422, "INVALID_REPORT_RANGE", "日期超出业务时区可表示范围") from exc
    return {
        "org": ctx.organization_id,
        "date_from": scope["date_from"],
        "date_to": scope["date_to"],
        "timezone": zone.key,
        "start": start,
        "end": end,
    }


SALES_CTE = (
    """WITH sales_facts AS (
 SELECT sl.id,sd.id document_id,sd.order_id,d.number,sd.kind,d.posted_at,
  (d.posted_at AT TIME ZONE :timezone)::date date,
  l.product_id,l.product_label label,l.unit_label unit_name,l.qty,
  o.customer_name party_name,sl.amount,"""
    + ACTUAL_COST
    + """ actual_cost
 FROM forge.sales_documents sd JOIN forge.inventory_documents d
 ON (d.organization_id,d.id)=(sd.organization_id,sd.id)
 JOIN forge.sales_document_lines sl
 ON (sl.organization_id,sl.document_id)=(sd.organization_id,sd.id)
 JOIN forge.inventory_document_lines l ON (l.organization_id,l.id)=(sl.organization_id,sl.id)
 JOIN forge.sales_orders o ON (o.organization_id,o.id)=(sd.organization_id,sd.order_id)
 WHERE sd.organization_id=:org AND d.status='POSTED' AND """
    + PUBLIC_DOCUMENTS
    + """
 AND d.posted_at>=:start AND d.posted_at<:end
) """
)


def sales_values(row, ctx):
    result = {key: row[key] for key in ("shipment_count", "return_count")}
    if "product.price.read" in ctx.permissions:
        result.update(shipment_amount=row["shipment_amount"], return_amount=row["return_amount"])
        result["net_sales_amount"] = row["shipment_amount"] - row["return_amount"]
        if "product.cost.read" in ctx.permissions:
            result["cost_status"] = "MISSING_FACTS" if row["missing_cost_count"] else "AVAILABLE"
            if not row["missing_cost_count"]:
                result.update(shipment_cost=row["shipment_cost"], return_cost=row["return_cost"])
                result["net_cost"] = row["shipment_cost"] - row["return_cost"]
                result["gross_margin"] = result["net_sales_amount"] - result["net_cost"]
    return result


async def sales_period(db, ctx, scope):
    ctx.require("sales.read")
    rows = (
        (
            await db.execute(
                text(
                    SALES_CTE
                    + """
 SELECT date,count(DISTINCT document_id) FILTER(WHERE kind='SHIPMENT') shipment_count,
 count(DISTINCT document_id) FILTER(WHERE kind='RETURN') return_count,
 coalesce(sum(amount) FILTER(WHERE kind='SHIPMENT'),0) shipment_amount,
 coalesce(sum(amount) FILTER(WHERE kind='RETURN'),0) return_amount,
 coalesce(sum(actual_cost) FILTER(WHERE kind='SHIPMENT'),0) shipment_cost,
 coalesce(sum(actual_cost) FILTER(WHERE kind='RETURN'),0) return_cost,
 count(*) FILTER(WHERE actual_cost IS NULL) missing_cost_count
 FROM sales_facts GROUP BY date ORDER BY date
 """
                ),
                scope_params(ctx, scope),
            )
        )
        .mappings()
        .all()
    )
    by_day = {r["date"]: r for r in rows}
    numeric_keys = ("shipment_amount", "return_amount", "shipment_cost", "return_cost")
    count_keys = ("shipment_count", "return_count", "missing_cost_count")
    empty = {**dict.fromkeys(numeric_keys, ZERO), **dict.fromkeys(count_keys, 0)}
    with localcontext() as dec:
        dec.prec = 70
        total = {key: sum((row[key] for row in rows), empty[key]) for key in empty}
        days = [
            scope["date_from"] + timedelta(days=i)
            for i in range((scope["date_to"] - scope["date_from"]).days + 1)
        ]
        return {
            **sales_values(total, ctx),
            "daily": [{"date": day, **sales_values(by_day.get(day, empty), ctx)} for day in days],
        }


async def integration_state(db, ctx, side):
    ctx.require("funds.ar.read" if side == "AR" else "funds.ap.read")
    prefix, kind = ("sales", "SHIPMENT") if side == "AR" else ("purchase", "RECEIPT")
    row = (
        (
            await db.execute(
                text(f"""
 SELECT EXISTS(SELECT 1 FROM forge.funds_activation WHERE organization_id=:org) enabled,
 (SELECT count(*) FROM forge.{prefix}_documents x JOIN forge.inventory_documents d
 ON (d.organization_id,d.id)=(x.organization_id,x.id)
 WHERE x.organization_id=:org AND x.kind=:kind AND d.status='POSTED' AND NOT EXISTS(
 SELECT 1 FROM forge.funds_sources s WHERE s.organization_id=:org
 AND s.source_document_id=x.id)) unmapped_document_count
 """),
                {"org": ctx.organization_id, "kind": kind},
            )
        )
        .mappings()
        .one()
    )
    return {
        "integration_status": "NOT_ENABLED"
        if not row["enabled"]
        else "INCOMPLETE"
        if row["unmapped_document_count"]
        else "ACTIVE",
        "unmapped_document_count": row["unmapped_document_count"],
    }


CASH_FROM = """FROM forge.funds_cash_documents d
 LEFT JOIN forge.funds_cash_reversals r
 ON (r.organization_id,r.cash_id)=(d.organization_id,d.id)
 WHERE d.organization_id=:org AND d.side=:side AND r.id IS NULL
 AND d.business_date>=:date_from AND d.business_date<=:date_to """


async def cash_period(db, ctx, side, scope, state):
    if state["integration_status"] == "NOT_ENABLED":
        return {**state, "daily": []}
    rows = (
        (
            await db.execute(
                text(
                    """
 SELECT d.business_date date,
 coalesce(sum(d.amount) FILTER(WHERE d.kind='SETTLEMENT'),0) settlement_amount,
 coalesce(sum(d.amount) FILTER(WHERE d.kind='REFUND'),0) refund_amount
 """
                    + CASH_FROM
                    + " GROUP BY d.business_date ORDER BY d.business_date"
                ),
                {**scope_params(ctx, scope), "side": side},
            )
        )
        .mappings()
        .all()
    )
    by_day = {r["date"]: r for r in rows}
    days = [
        scope["date_from"] + timedelta(days=i)
        for i in range((scope["date_to"] - scope["date_from"]).days + 1)
    ]
    with localcontext() as dec:
        dec.prec = 70
        total_settlement = sum((r["settlement_amount"] for r in rows), ZERO)
        total_refund = sum((r["refund_amount"] for r in rows), ZERO)
        daily = []
        for day in days:
            row = by_day.get(day, {"settlement_amount": ZERO, "refund_amount": ZERO})
            daily.append(
                {
                    "date": day,
                    "settlement_amount": row["settlement_amount"],
                    "refund_amount": row["refund_amount"],
                    "net_cash_amount": row["settlement_amount"] - row["refund_amount"],
                }
            )
        return {
            **state,
            "settlement_amount": total_settlement,
            "refund_amount": total_refund,
            "net_cash_amount": total_settlement - total_refund,
            "daily": daily,
        }


async def current_funds(db, ctx, side, state):
    if state["integration_status"] == "NOT_ENABLED":
        return dict(state)
    row = (
        (
            await db.execute(
                text(
                    SOURCE_CTE
                    + """
 SELECT count(*) source_count,coalesce(sum(balance),0) balance,
 coalesce(sum(settlement_amount),0) settlement_amount,
 coalesce(sum(refund_amount),0) refund_amount FROM source_view WHERE side=:side
 """
                ),
                {"org": ctx.organization_id, "side": side},
            )
        )
        .mappings()
        .one()
    )
    return {**state, **row}


LOW_STOCK_CTE = """WITH low_stock AS (
 SELECT p.id,p.sku number,p.name label,u.name unit_name,p.min_stock_qty,
 coalesce(b.available_qty,0) available_qty FROM forge.products p JOIN forge.units u
 ON (u.organization_id,u.id)=(p.organization_id,p.base_unit_id)
 LEFT JOIN (SELECT organization_id,product_id,sum(on_hand_qty-reserved_qty) available_qty
 FROM forge.inventory_balances WHERE organization_id=:org GROUP BY organization_id,product_id) b
 ON (b.organization_id,b.product_id)=(p.organization_id,p.id)
 WHERE p.organization_id=:org AND p.active AND p.min_stock_qty>0
 AND coalesce(b.available_qty,0)<=p.min_stock_qty
) """


async def inventory_current(db, ctx):
    ctx.require("inventory.read")
    row = dict(
        (
            await db.execute(
                text(
                    LOW_STOCK_CTE
                    + """
 SELECT count(DISTINCT product_id) product_count,coalesce(sum(inventory_value),0) valuation,
 (SELECT count(*) FROM low_stock) low_stock_count
 FROM forge.inventory_balances WHERE organization_id=:org
 """
                ),
                {"org": ctx.organization_id},
            )
        )
        .mappings()
        .one()
    )
    if "product.cost.read" not in ctx.permissions:
        row.pop("valuation")
    return row


async def overview(db, ctx, date_from=None, date_to=None):
    ctx.require("dashboard.read")
    scope = report_scope(date_from, date_to)
    result = dict(scope)
    if "sales.read" in ctx.permissions:
        result["sales"] = await sales_period(db, ctx, scope)
    for side, suffix in (("AR", "ar"), ("AP", "ap")):
        if f"funds.{suffix}.read" in ctx.permissions:
            state = await integration_state(db, ctx, side)
            result[f"cash_{suffix}"] = await cash_period(db, ctx, side, scope, state)
            result[f"current_{suffix}"] = await current_funds(db, ctx, side, state)
    if "inventory.read" in ctx.permissions:
        result["inventory"] = await inventory_current(db, ctx)
    if REPL_PERMISSIONS <= ctx.permissions:
        from forge_erp.modules.replenishment.application.queries import count_suggestions

        result["replenishment"] = await count_suggestions(db, ctx, scope["as_of"])
    return result


async def source_page(db, sql, params, page, page_size):
    # Count and page share one statement and the caller's read-only snapshot.
    rows = (
        (
            await db.execute(
                text(
                    sql
                    + """,
 selected AS (SELECT * FROM filtered ORDER BY number,id LIMIT :limit OFFSET :offset)
 SELECT p.*,n.total FROM (SELECT count(*) total FROM filtered) n
 LEFT JOIN selected p ON true ORDER BY p.number,p.id
 """
                ),
                {**params, "limit": page_size, "offset": (page - 1) * page_size},
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [dict(r) for r in rows if r["id"] is not None],
        "total": rows[0]["total"],
        "page": page,
        "page_size": page_size,
    }


async def sources(db, ctx, metric, date_from=None, date_to=None, page=1, page_size=25):
    ctx.require("dashboard.read")
    scope = report_scope(date_from, date_to)
    params = scope_params(ctx, scope)
    extra = {}
    if metric == "sales":
        ctx.require("sales.read")
        sql = (
            SALES_CTE + ", filtered AS (SELECT *,CASE WHEN kind='SHIPMENT' THEN amount "
            "ELSE -amount END signed_amount,CASE WHEN kind='SHIPMENT' THEN actual_cost "
            "ELSE -actual_cost END signed_cost FROM sales_facts)"
        )
    elif metric in {"cash_ar", "cash_ap", "current_ar", "current_ap"}:
        side = "AR" if metric.endswith("_ar") else "AP"
        params["side"] = side
        extra = await integration_state(db, ctx, side)
        if extra["integration_status"] == "NOT_ENABLED":
            return {
                **scope,
                "metric": metric,
                "items": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                **extra,
            }
        if metric.startswith("cash_"):
            sql = (
                "WITH filtered AS (SELECT d.id,d.number,d.kind,d.party_name label,"
                "d.party_name,d.business_date date,d.amount,CASE WHEN d.kind='SETTLEMENT' "
                "THEN d.amount ELSE -d.amount END signed_amount " + CASH_FROM + ")"
            )
        else:
            sql = (
                SOURCE_CTE + ", filtered AS (SELECT id,number,kind,party_name label,party_name,"
                "source_document_id document_id,business_date date,balance,"
                "settlement_amount,refund_amount FROM source_view WHERE side=:side)"
            )
    elif metric == "inventory":
        ctx.require("inventory.read")
        sql = """WITH filtered AS (
        SELECT b.id,p.id product_id,p.sku number,'BALANCE' kind,p.name label,u.name unit_name,
        w.name warehouse_name,b.on_hand_qty qty,b.reserved_qty,
        b.on_hand_qty-b.reserved_qty available_qty,b.inventory_value valuation
        FROM forge.inventory_balances b JOIN forge.products p
        ON (p.organization_id,p.id)=(b.organization_id,b.product_id)
        JOIN forge.units u ON (u.organization_id,u.id)=(p.organization_id,p.base_unit_id)
        JOIN forge.warehouses w ON (w.organization_id,w.id)=(b.organization_id,b.warehouse_id)
        WHERE b.organization_id=:org)"""
    elif metric == "low_stock":
        ctx.require("inventory.read")
        sql = (
            LOW_STOCK_CTE + ", filtered AS (SELECT *,id product_id,'LOW_STOCK' kind FROM low_stock)"
        )
    elif metric == "replenishment":
        for permission in sorted(REPL_PERMISSIONS):
            ctx.require(permission)
        from forge_erp.modules.replenishment.application.queries import report_sources

        return {
            **scope,
            "metric": metric,
            **await report_sources(db, ctx, scope["as_of"], page, page_size),
        }
    else:
        raise Problem(422, "INVALID_REPORT_METRIC", "经营指标无效")
    result = await source_page(db, sql, params, page, page_size)
    with localcontext() as dec:
        dec.prec = 70
        for row in result["items"]:
            row.pop("total", None)
            if metric == "sales":
                if "product.price.read" not in ctx.permissions:
                    for field in ("amount", "signed_amount", "actual_cost", "signed_cost"):
                        row.pop(field, None)
                elif "product.cost.read" not in ctx.permissions:
                    row.pop("actual_cost", None)
                    row.pop("signed_cost", None)
                else:
                    row["cost_status"] = (
                        "AVAILABLE" if row["actual_cost"] is not None else "MISSING_FACTS"
                    )
                    if row["actual_cost"] is not None:
                        row["gross_margin"] = row["signed_amount"] - row["signed_cost"]
            if metric == "inventory" and "product.cost.read" not in ctx.permissions:
                row.pop("valuation", None)
    return {**scope, "metric": metric, **result, **extra}
