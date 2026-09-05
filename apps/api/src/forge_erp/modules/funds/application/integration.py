"""Synchronous funds effects inside authorized commercial posting transactions.

Callers acquire the cutover lock before their existing order/document/stock locks.
These internal hooks never commit, queue core writes, or expose cash permissions.
"""

from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text

from forge_erp.core.config import settings
from forge_erp.core.errors import Problem
from forge_erp.modules.audit.service import record_mutation
from forge_erp.modules.funds.application import queries
from forge_erp.modules.funds.application import shared as f


def posting_permission(ctx, side, kind):
    if side == "AR" and kind in {"SHIPMENT", "RETURN"}:
        ctx.require("sales.ship" if kind == "SHIPMENT" else "sales.return")
    elif side == "AP" and kind in {"RECEIPT", "RETURN"}:
        ctx.require("purchase.receive" if kind == "RECEIPT" else "purchase.return")
    else:
        raise Problem(409, "INVALID_SOURCE", "资金接入的商业来源方向不一致")


async def linked_source(db, ctx, side, document):
    origin_id = document["original_document_id"] or document["id"]
    row = (
        await db.execute(
            text(
                "SELECT id FROM forge.funds_sources WHERE organization_id=:org "
                "AND source_document_id=:origin"
            ),
            {"org": ctx.organization_id, "origin": origin_id},
        )
    ).scalar_one_or_none()
    if row is None:
        raise Problem(
            409,
            "LEGACY_FUNDS_MAPPING_REQUIRED",
            "原单尚未接入资金期初，请在资金工作台完成历史来源绑定后重试",
        )
    source = await f.source(db, ctx, row)
    if source["side"] != side or source["party_id"] != document["party_id"]:
        raise Problem(409, "INVALID_SOURCE", "资金来源与原单往来对象不一致")
    if source["voided"]:
        raise Problem(409, "INVALID_SOURCE", "资金来源已冲销，不能继续接入商业单据")
    return source


async def post(db, ctx, side, id):
    if await f.cutover(db, ctx) is None:
        return
    doc = await queries.commercial_document(db, ctx, side, id)
    posting_permission(ctx, side, doc["kind"])
    if doc["status"] != "POSTED":
        raise Problem(409, "INVALID_SOURCE", "仅已过账商业单据可接入资金")
    await f.lock_party(db, ctx, side, doc["party_id"])
    amount = f.money(doc["commercial_amount"], nonnegative=True)
    explanation = "商业单据过账：" + doc["number"]
    if doc["kind"] in {"SHIPMENT", "RECEIPT"}:
        party = await f.party(db, ctx, side, doc["party_id"])
        source_id = await f.add_source(
            db,
            ctx,
            side,
            party,
            doc["kind"],
            amount,
            doc["posted_at"].astimezone(ZoneInfo(settings().business_timezone)).date(),
            explanation,
            document=doc,
            commercial_amount=amount,
        )
        event = "funds.source.post"
    else:
        source = await linked_source(db, ctx, side, doc)
        source_id = source["id"]
        await f.add_entry(db, ctx, source_id, "RETURN", -amount, explanation, document_id=id)
        event = "funds.source.return"
    await record_mutation(
        db,
        ctx,
        event,
        "funds_source",
        source_id,
        None,
        {"version": 1, "document_id": id, "side": side, "commercial_amount": amount},
    )


async def reverse(db, ctx, side, id):
    if await f.cutover(db, ctx) is None:
        return
    doc = await queries.commercial_document(db, ctx, side, id)
    posting_permission(ctx, side, doc["kind"])
    ctx.require("sales.reverse" if side == "AR" else "purchase.reverse")
    if doc["status"] not in {"POSTED", "REVERSED"}:
        raise Problem(409, "INVALID_SOURCE", "仅已过账商业单据可接入资金冲销")
    await f.lock_party(db, ctx, side, doc["party_id"])
    source = await linked_source(db, ctx, side, doc)
    if await f.active_allocations(db, ctx, source["id"]):
        raise Problem(
            409,
            "FUNDS_DEPENDENCY_CONFLICT",
            "原资金来源已有有效收付款或退款，请先处理对应资金冲销后重试",
        )
    original_kind = "RETURN" if doc["kind"] == "RETURN" else "ORIGIN"
    entry = (
        (
            await db.execute(
                text(
                    "SELECT id,amount FROM forge.funds_entries WHERE organization_id=:org "
                    "AND source_id=:source AND document_id=:document AND kind=:kind"
                ),
                {
                    "org": ctx.organization_id,
                    "source": source["id"],
                    "document": id,
                    "kind": original_kind,
                },
            )
        )
        .mappings()
        .first()
    )
    reverses_id = None
    if entry:
        delta = -entry["amount"]
        reverses_id = entry["id"]
    elif source["kind"] == "LEGACY":
        snapshot = source["legacy_snapshot"]
        if doc["kind"] == "RETURN":
            old_return = next(
                (r for r in snapshot.get("returns", []) if str(r["id"]) == str(id)), None
            )
            if old_return is None:
                raise Problem(409, "INVALID_SOURCE", "历史退货不在已确认的期初快照中")
            delta = Decimal(old_return["amount"])
        else:
            # Historical returns reversed since the cutover have already added
            # their amounts back. Reversing the gross original removes them once.
            delta = -Decimal(snapshot["commercial_amount"])
    else:
        raise Problem(409, "INVALID_SOURCE", "商业单据对应的资金事实不完整")
    explanation = "商业单据冲销：" + doc["number"]
    await f.add_entry(
        db,
        ctx,
        source["id"],
        "DOCUMENT_REVERSE",
        delta,
        explanation,
        document_id=id,
        reverses_id=reverses_id,
    )
    await record_mutation(
        db,
        ctx,
        "funds.source.document.reverse",
        "funds_source",
        source["id"],
        None,
        {"version": 1, "document_id": id, "side": side, "amount": delta},
    )
