import json
from decimal import Decimal, localcontext
from uuid import uuid4

from sqlalchemy import text

from forge_erp.core.errors import Problem
from forge_erp.core.idempotency import execute_once
from forge_erp.core.security import fingerprint

ZERO = Decimal("0.0000")
SCALE = Decimal("0.0001")


def money(value, *, positive=False, nonnegative=False):
    if isinstance(value, float):
        raise Problem(422, "INVALID_AMOUNT", "金额必须使用十进制字符串")
    value = Decimal(value)
    with localcontext() as precision:
        precision.prec = 50
        if not value.is_finite() or abs(value) >= Decimal("1e16") or value != value.quantize(SCALE):
            raise Problem(422, "INVALID_AMOUNT", "金额最多四位小数，绝对值必须小于 10^16")
        if (positive and value <= 0) or (nonnegative and value < 0):
            raise Problem(
                422, "INVALID_AMOUNT", "本次金额必须大于零" if positive else "金额不能为负数"
            )
        return value.quantize(SCALE)


def reason(value):
    value = value.strip()
    if not value or len(value) > 2000:
        raise Problem(422, "REASON_REQUIRED", "请填写有效的业务说明")
    return value


def require_read(ctx, side):
    ctx.require("funds.ar.read" if side == "AR" else "funds.ap.read")


def cash_permission(side, kind):
    if kind == "REFUND":
        return "funds.customer_refund" if side == "AR" else "funds.supplier_refund"
    return "funds.receive" if side == "AR" else "funds.pay"


def receipt(ctx, id, status="POSTED"):
    return {"id": str(id), "status": status, "request_id": ctx.request_id}


def number(prefix):
    return prefix + "-" + uuid4().hex[:16].upper()


async def cutover(db, ctx, *, exclusive=False, required=False):
    # Lock even before the activation row exists. Classification is the lock order,
    # not now()/posted_at (which can be the start of an older transaction).
    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    await db.execute(
        text(f"SELECT {function}(hashtextextended(:key,0))"),
        {"key": f"funds-cutover:{ctx.organization_id}"},
    )
    row = (
        (
            await db.execute(
                text("SELECT * FROM forge.funds_activation WHERE organization_id=:org"),
                {"org": ctx.organization_id},
            )
        )
        .mappings()
        .first()
    )
    if required and row is None:
        raise Problem(409, "FUNDS_NOT_ENABLED", "请先明确资金启用日期和期初衔接方式")
    return dict(row) if row else None


async def lock_party(db, ctx, side, party_id, *, shared=False):
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    await db.execute(
        text(f"SELECT {function}(hashtextextended(:key,0))"),
        {"key": f"funds-party:{ctx.organization_id}:{side}:{party_id}"},
    )


async def party(db, ctx, side, party_id):
    table = "customers" if side == "AR" else "suppliers"
    row = (
        (
            await db.execute(
                text(
                    f"SELECT id,code,name,active FROM forge.{table} "
                    "WHERE organization_id=:org AND id=:id"
                ),
                {"org": ctx.organization_id, "id": party_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "NOT_FOUND", "往来对象不存在或无权访问")
    # An inactive master may still have real outstanding money to settle/refund.
    return dict(row)


async def once(db, ctx, operation, key, body, command):
    payload = body.model_dump(mode="json") if hasattr(body, "model_dump") else body

    async def durable():
        params = {"org": ctx.organization_id, "actor": ctx.user_id, "op": operation, "key": key}
        previous = (
            (
                await db.execute(
                    text(
                        "SELECT request_hash,response FROM forge.funds_operations WHERE "
                        "organization_id=:org AND actor_id=:actor AND operation=:op AND key=:key"
                    ),
                    params,
                )
            )
            .mappings()
            .first()
        )
        digest = fingerprint(payload)
        if previous:
            if previous["request_hash"] != digest:
                raise Problem(409, "IDEMPOTENCY_KEY_REUSED", "原资金操作键对应不同内容，请核对原单")
            return dict(previous["response"])
        result = await command()
        await db.execute(
            text(
                "INSERT INTO forge.funds_operations(organization_id,actor_id,operation,key,"
                "request_hash,response) VALUES(:org,:actor,:op,:key,:hash,CAST(:response AS jsonb))"
            ),
            {**params, "hash": digest, "response": json.dumps(result, default=str)},
        )
        return result

    return await execute_once(db, ctx, operation, key, payload, durable)


# Every amount is returned as PostgreSQL NUMERIC -> Decimal; JSON SQL aggregation
# must not turn money into binary floating point. Cash status comes from reversals.
SOURCE_CTE = """
WITH entry_totals AS (
 SELECT source_id,sum(amount) amount,bool_or(kind='VOID') voided
 FROM forge.funds_entries WHERE organization_id=:org GROUP BY source_id
), cash_totals AS (
 SELECT a.source_id,
  coalesce(sum(a.amount) FILTER(WHERE d.kind='SETTLEMENT'),0) settled_amount,
  coalesce(sum(a.amount) FILTER(WHERE d.kind='REFUND'),0) refunded_amount
 FROM forge.funds_cash_allocations a JOIN forge.funds_cash_documents d
  ON d.organization_id=a.organization_id AND d.id=a.cash_id
 LEFT JOIN forge.funds_cash_reversals r ON r.organization_id=d.organization_id AND r.cash_id=d.id
 WHERE a.organization_id=:org AND r.id IS NULL GROUP BY a.source_id
), balances AS (
 SELECT s.*, coalesce(e.amount,0) amount,coalesce(e.voided,false) voided,
  coalesce((s.legacy_snapshot->>'historically_settled_amount')::numeric,0)
   historically_settled_amount,
  coalesce(c.settled_amount,0) settled_amount,coalesce(c.refunded_amount,0) refunded_amount,
  coalesce(e.amount,0)-coalesce(c.settled_amount,0)+coalesce(c.refunded_amount,0) balance
 FROM forge.funds_sources s LEFT JOIN entry_totals e ON e.source_id=s.id
 LEFT JOIN cash_totals c ON c.source_id=s.id WHERE s.organization_id=:org
), source_view AS (
 SELECT *,greatest(balance,0) settlement_amount,greatest(-balance,0) refund_amount,
  CASE WHEN balance<=0 THEN 'PAID'
   WHEN historically_settled_amount+settled_amount-refunded_amount>0 THEN 'PARTIAL'
   ELSE 'UNPAID' END settlement_status,
  CASE WHEN voided THEN 'REVERSED' WHEN balance>0 THEN 'OPEN'
   WHEN balance<0 THEN 'REFUND' ELSE 'SETTLED' END status FROM balances
)
"""


async def source(db, ctx, id):
    row = (
        (
            await db.execute(
                text(SOURCE_CTE + "SELECT * FROM source_view WHERE id=:id"),
                {"org": ctx.organization_id, "id": id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "NOT_FOUND", "资金来源不存在或无权访问")
    return dict(row)


async def active_allocations(db, ctx, source_id):
    return (
        await db.execute(
            text(
                "SELECT count(*) FROM forge.funds_cash_allocations a "
                "LEFT JOIN forge.funds_cash_reversals r ON r.organization_id=a.organization_id "
                "AND r.cash_id=a.cash_id WHERE a.organization_id=:org "
                "AND a.source_id=:id AND r.id IS NULL"
            ),
            {"org": ctx.organization_id, "id": source_id},
        )
    ).scalar_one()


async def add_entry(
    db,
    ctx,
    source_id,
    kind,
    amount,
    explanation,
    *,
    document_id=None,
    related_source_id=None,
    reverses_id=None,
):
    return (
        await db.execute(
            text(
                "INSERT INTO forge.funds_entries(organization_id,source_id,kind,amount,reason,"
                "document_id,related_source_id,reverses_id,created_by) "
                "VALUES(:org,:source,:kind,:amount,:reason,:document,:related,:reverses,:actor) "
                "RETURNING id"
            ),
            {
                "org": ctx.organization_id,
                "source": source_id,
                "kind": kind,
                "amount": money(amount),
                "reason": reason(explanation),
                "document": document_id,
                "related": related_source_id,
                "reverses": reverses_id,
                "actor": ctx.user_id,
            },
        )
    ).scalar_one()


async def add_source(
    db,
    ctx,
    side,
    party_row,
    kind,
    amount,
    business_date,
    explanation,
    *,
    document=None,
    snapshot=None,
    commercial_amount=ZERO,
):
    source_id = (
        await db.execute(
            text(
                "INSERT INTO forge.funds_sources(organization_id,number,side,party_id,customer_id,"
                "supplier_id,party_name,kind,source_document_id,source_document_number,commercial_amount,"
                "business_date,reason,legacy_snapshot,created_by) VALUES(:org,:number,:side,:party,"
                ":customer,:supplier,:name,:kind,:document,:docnumber,:commercial,:date,:reason,"
                "CAST(:snapshot AS jsonb),:actor) RETURNING id"
            ),
            {
                "org": ctx.organization_id,
                "number": number(side),
                "side": side,
                "party": party_row["id"],
                "customer": party_row["id"] if side == "AR" else None,
                "supplier": party_row["id"] if side == "AP" else None,
                "name": party_row["name"],
                "kind": kind,
                "document": document["id"] if document else None,
                "docnumber": document["number"] if document else None,
                "commercial": money(commercial_amount, nonnegative=True),
                "date": business_date,
                "reason": reason(explanation),
                "snapshot": json.dumps(snapshot or {}, default=str),
                "actor": ctx.user_id,
            },
        )
    ).scalar_one()
    await add_entry(
        db,
        ctx,
        source_id,
        "ORIGIN",
        amount,
        explanation,
        document_id=document["id"] if document and kind != "LEGACY" else None,
    )
    return source_id
