"""Funds mutation commands; each caller owns the surrounding transaction."""

from datetime import datetime
from decimal import localcontext
from zoneinfo import ZoneInfo

from sqlalchemy import text

from forge_erp.core.config import settings
from forge_erp.core.errors import Problem
from forge_erp.modules.audit.service import record_mutation
from forge_erp.modules.funds.application import queries
from forge_erp.modules.funds.application import shared as f


async def audit(db, ctx, action, id, after, before=None):
    await record_mutation(db, ctx, action, "funds", id, before, {"version": 1, **after})


async def activate(db, ctx, body, key):
    ctx.require("funds.activate")

    async def execute():
        if body.business_date > datetime.now(ZoneInfo(settings().business_timezone)).date():
            raise Problem(422, "FUTURE_FUNDS_CUTOVER", "资金启用业务日期不能晚于当前营业日期")
        previous = await f.cutover(db, ctx, exclusive=True)
        if previous:
            raise Problem(409, "FUNDS_ALREADY_ENABLED", "资金已经启用；启用切点不能重写")
        id = (
            await db.execute(
                text(
                    "INSERT INTO forge.funds_activation "
                    "(organization_id,business_date,reason,created_by) "
                    "VALUES(:org,:date,:reason,:actor) RETURNING id"
                ),
                {
                    "org": ctx.organization_id,
                    "date": body.business_date,
                    "reason": f.reason(body.reason),
                    "actor": ctx.user_id,
                },
            )
        ).scalar_one()
        await audit(db, ctx, "funds.activate", id, body.model_dump(mode="json"))
        return f.receipt(ctx, id, "ENABLED")

    return await f.once(db, ctx, "funds.activate", key, body, execute)


async def opening(db, ctx, body, key, adjustment=False):
    operation = "funds.adjustment" if adjustment else "funds.opening"
    ctx.require("funds.adjust" if adjustment else "funds.opening")

    async def execute():
        activation = await f.cutover(db, ctx, required=True)
        assert activation is not None
        amount = f.money(body.amount)
        if amount == 0:
            raise Problem(422, "INVALID_AMOUNT", "零余额无需期初行；已结清历史原单请使用零余额绑定")
        if amount < 0 and not body.refund_balance_confirmed:
            raise Problem(
                422, "REFUND_BALANCE_CONFIRMATION_REQUIRED", "负余额代表明确待退款，请确认"
            )
        await f.lock_party(db, ctx, body.side, body.party_id)
        party = await f.party(db, ctx, body.side, body.party_id)
        id = await f.add_source(
            db,
            ctx,
            body.side,
            party,
            "ADJUSTMENT" if adjustment else "OPENING",
            amount,
            datetime.now(ZoneInfo(settings().business_timezone)).date()
            if adjustment
            else activation["business_date"],
            body.reason,
        )
        await audit(db, ctx, operation, id, {**body.model_dump(mode="json"), "amount": amount})
        return f.receipt(ctx, id)

    return await f.once(db, ctx, operation, key, body, execute)


async def bind_legacy(db, ctx, body, key):
    ctx.require("funds.opening")

    async def execute():
        activation = await f.cutover(db, ctx, required=True)
        assert activation is not None
        doc = await queries.commercial_document(
            db, ctx, body.side, body.source_document_id, lock=True
        )
        if (
            doc["kind"] != ("SHIPMENT" if body.side == "AR" else "RECEIPT")
            or doc["status"] != "POSTED"
        ):
            raise Problem(409, "INVALID_LEGACY_SOURCE", "只能绑定尚未接入资金的有效原出库或原收货")
        await f.lock_party(db, ctx, body.side, doc["party_id"])
        previous = (
            await db.execute(
                text(
                    "SELECT id FROM forge.funds_sources "
                    "WHERE organization_id=:org AND source_document_id=:id"
                ),
                {"org": ctx.organization_id, "id": doc["id"]},
            )
        ).scalar_one_or_none()
        if previous:
            raise Problem(
                409, "FUNDS_SOURCE_ALREADY_MAPPED", "该原单已有资金来源，请打开已有资金来源核对"
            )
        amount = f.money(body.amount)
        effective = doc["commercial_amount"] - doc["returned_amount"]
        if amount > effective or amount < -doc["returned_amount"]:
            raise Problem(
                409,
                "LEGACY_AMOUNT_EXCEEDED",
                "正欠款不能超过历史原单有效净额，待退款不能超过原单已退商业金额",
            )
        if amount < 0 and not body.refund_balance_confirmed:
            raise Problem(
                422,
                "REFUND_BALANCE_CONFIRMATION_REQUIRED",
                "负余额表示旧原单仍待退款，请明确确认",
            )
        if amount != 0 and body.opening_source_id is None:
            raise Problem(
                422, "OPENING_SOURCE_REQUIRED", "请选择同对象尚未核销的期初来源；绑定不新增余额"
            )
        donor = None
        if body.opening_source_id:
            donor = await f.source(db, ctx, body.opening_source_id)
            if (
                donor["side"] != body.side
                or donor["party_id"] != doc["party_id"]
                or donor["kind"] not in {"OPENING", "ADJUSTMENT"}
                or donor["voided"]
            ):
                raise Problem(
                    409, "INVALID_OPENING_SOURCE", "只能转移同对象有效期初或明确调整的未核销余额"
                )
            available = donor["settlement_amount"] if amount >= 0 else donor["refund_amount"]
            if available < abs(amount):
                raise Problem(
                    409,
                    "OPENING_BALANCE_EXCEEDED",
                    "同方向期初未核销或未退款余额不足；请先核对既有收付或明确调整",
                )
        prefix = "sales" if body.side == "AR" else "purchase"
        returns = [
            dict(row)
            for row in (
                await db.execute(
                    text(
                        f"SELECT r.id,sum(l.amount) amount "
                        f"FROM forge.{prefix}_documents r JOIN forge.inventory_documents d "
                        "ON d.organization_id=r.organization_id AND d.id=r.id "
                        f"JOIN forge.{prefix}_document_lines l "
                        "ON l.organization_id=r.organization_id AND l.document_id=r.id "
                        "WHERE r.organization_id=:org AND r.original_document_id=:id "
                        "AND d.status='POSTED' GROUP BY r.id"
                    ),
                    {"org": ctx.organization_id, "id": doc["id"]},
                )
            ).mappings()
        ]
        party = {"id": doc["party_id"], "name": doc["party_name"]}
        id = await f.add_source(
            db,
            ctx,
            body.side,
            party,
            "LEGACY",
            amount,
            activation["business_date"],
            body.reason,
            document=doc,
            commercial_amount=doc["commercial_amount"],
            snapshot={
                "commercial_amount": doc["commercial_amount"],
                "returned_amount": doc["returned_amount"],
                "effective_amount": effective,
                "opening_amount": amount,
                "historically_settled_amount": effective - amount,
                "returns": returns,
                "opening_source_id": body.opening_source_id,
            },
        )
        if donor and amount:
            await f.add_entry(
                db, ctx, donor["id"], "TRANSFER_OUT", -amount, body.reason, related_source_id=id
            )
        await audit(db, ctx, "funds.legacy.bind", id, body.model_dump(mode="json"))
        return f.receipt(ctx, id)

    return await f.once(db, ctx, "funds.legacy.bind", key, body, execute)


async def validate_cash(db, ctx, body, *, shared=False):
    activation = await f.cutover(db, ctx, required=True)
    assert activation is not None
    if body.business_date < activation["business_date"]:
        raise Problem(422, "BEFORE_FUNDS_CUTOVER", "收付款业务日期不能早于资金启用业务日期")
    f.reason(body.reason)
    await f.lock_party(db, ctx, body.side, body.party_id, shared=shared)
    party = await f.party(db, ctx, body.side, body.party_id)
    if len({line.source_id for line in body.allocations}) != len(body.allocations):
        raise Problem(422, "DUPLICATE_SOURCE", "同一资金来源不能重复核销")
    lines = []
    with localcontext() as precision:
        precision.prec = 50
        total = f.ZERO
        for line in body.allocations:
            amount = f.money(line.amount, positive=True)
            source = await f.source(db, ctx, line.source_id)
            if (
                source["side"] != body.side
                or source["party_id"] != body.party_id
                or source["voided"]
            ):
                raise Problem(
                    409, "INVALID_ALLOCATION_SOURCE", "只能选择同方向、同对象的有效资金来源"
                )
            available = (
                source["settlement_amount"]
                if body.kind == "SETTLEMENT"
                else source["refund_amount"]
            )
            if amount > available:
                raise Problem(
                    409, "ALLOCATION_EXCEEDED", "本次收付超过来源当前可核销或可退款金额，请刷新核对"
                )
            lines.append(
                {
                    "source_id": line.source_id,
                    "source_number": source["number"],
                    "amount": amount,
                    "available_amount": available,
                }
            )
            total += amount
        total = f.money(total, positive=True)
    return {
        "side": body.side,
        "party_id": body.party_id,
        "party_name": party["name"],
        "kind": body.kind,
        "amount": total,
        "allocations": lines,
    }


async def preview_cash(db, ctx, body):
    ctx.require(f.cash_permission(body.side, body.kind))
    f.require_read(ctx, body.side)
    return await validate_cash(db, ctx, body, shared=True)


async def cash(db, ctx, body, key):
    ctx.require(f.cash_permission(body.side, body.kind))

    async def execute():
        validated = await validate_cash(db, ctx, body)
        id = (
            await db.execute(
                text(
                    "INSERT INTO forge.funds_cash_documents(organization_id,number,"
                    "side,party_id,customer_id,supplier_id,party_name,kind,amount,business_date,method,"
                    "external_reference,reason,created_by) "
                    "VALUES(:org,:number,:side,:party,:customer,:supplier,"
                    ":name,:kind,:amount,:date,:method,:external,:reason,:actor) RETURNING id"
                ),
                {
                    "org": ctx.organization_id,
                    "number": f.number("RC" if body.side == "AR" else "PC"),
                    "side": body.side,
                    "party": body.party_id,
                    "customer": body.party_id if body.side == "AR" else None,
                    "supplier": body.party_id if body.side == "AP" else None,
                    "name": validated["party_name"],
                    "kind": body.kind,
                    "amount": validated["amount"],
                    "date": body.business_date,
                    "method": body.method,
                    "external": body.external_reference,
                    "reason": body.reason,
                    "actor": ctx.user_id,
                },
            )
        ).scalar_one()
        for line in validated["allocations"]:
            await db.execute(
                text(
                    "INSERT INTO forge.funds_cash_allocations(organization_id,cash_id,"
                    "source_id,side,party_id,amount) "
                    "VALUES(:org,:cash,:source,:side,:party,:amount)"
                ),
                {
                    "org": ctx.organization_id,
                    "cash": id,
                    "source": line["source_id"],
                    "side": body.side,
                    "party": body.party_id,
                    "amount": line["amount"],
                },
            )
        await audit(
            db,
            ctx,
            "funds.cash.post",
            id,
            {**body.model_dump(mode="json"), "amount": validated["amount"]},
        )
        return f.receipt(ctx, id)

    return await f.once(db, ctx, "funds.cash.post", key, body, execute)


async def reverse_cash(db, ctx, id, explanation, key):
    ctx.require("funds.reverse")
    initial = await queries.raw_cash(db, ctx, id)
    ctx.require(f.cash_permission(initial["side"], initial["kind"]))

    async def execute():
        await f.cutover(db, ctx, required=True)
        await f.lock_party(db, ctx, initial["side"], initial["party_id"])
        current = await queries.raw_cash(db, ctx, id)
        if current["status"] != "POSTED":
            raise Problem(409, "INVALID_FUNDS_STATE", "原收付款已冲销，请查看原单与反向记录")
        later = (
            await db.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM forge.funds_cash_allocations a "
                    "JOIN forge.funds_cash_documents d "
                    "ON d.organization_id=a.organization_id AND d.id=a.cash_id "
                    "LEFT JOIN forge.funds_cash_reversals r "
                    "ON r.organization_id=d.organization_id AND r.cash_id=d.id "
                    "WHERE a.organization_id=:org AND d.sequence>:sequence AND r.id IS NULL "
                    "AND a.source_id IN (SELECT source_id FROM forge.funds_cash_allocations "
                    "WHERE organization_id=:org AND cash_id=:id))"
                ),
                {"org": ctx.organization_id, "sequence": current["sequence"], "id": id},
            )
        ).scalar_one()
        if later:
            raise Problem(
                409,
                "FUNDS_REVERSAL_DEPENDENCY",
                "同来源已有后续有效收付或退款，请先冲销后续资金记录",
            )
        await db.execute(
            text(
                "INSERT INTO forge.funds_cash_reversals(organization_id,cash_id,reason,created_by) "
                "VALUES(:org,:id,:reason,:actor)"
            ),
            {
                "org": ctx.organization_id,
                "id": id,
                "reason": f.reason(explanation),
                "actor": ctx.user_id,
            },
        )
        await audit(
            db,
            ctx,
            "funds.cash.reverse",
            id,
            {"reason": explanation, "status": "REVERSED"},
            current,
        )
        return f.receipt(ctx, id, "REVERSED")

    return await f.once(db, ctx, f"funds.cash.reverse:{id}", key, {"reason": explanation}, execute)


async def reverse_source(db, ctx, id, explanation, key):
    ctx.require("funds.reverse")
    initial = await f.source(db, ctx, id)
    ctx.require("funds.adjust" if initial["kind"] == "ADJUSTMENT" else "funds.opening")

    async def execute():
        await f.cutover(db, ctx, required=True)
        await f.lock_party(db, ctx, initial["side"], initial["party_id"])
        row = await f.source(db, ctx, id)
        if row["kind"] not in {"OPENING", "ADJUSTMENT"} or row["voided"]:
            raise Problem(
                409, "INVALID_FUNDS_STATE", "只能冲销有效期初或明确调整；商业来源请从原业务单据处理"
            )
        transferred = (
            await db.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM forge.funds_entries "
                    "WHERE organization_id=:org AND source_id=:id AND kind='TRANSFER_OUT')"
                ),
                {"org": ctx.organization_id, "id": id},
            )
        ).scalar_one()
        if transferred or await f.active_allocations(db, ctx, id):
            raise Problem(
                409,
                "FUNDS_REVERSAL_DEPENDENCY",
                "来源已有有效收付或历史绑定；请先处理依赖或做明确调整",
            )
        origin = (
            await db.execute(
                text(
                    "SELECT id FROM forge.funds_entries WHERE organization_id=:org "
                    "AND source_id=:id AND kind='ORIGIN'"
                ),
                {"org": ctx.organization_id, "id": id},
            )
        ).scalar_one()
        await f.add_entry(db, ctx, id, "VOID", -row["amount"], explanation, reverses_id=origin)
        await audit(
            db, ctx, "funds.source.reverse", id, {"reason": explanation, "status": "REVERSED"}, row
        )
        return f.receipt(ctx, id, "REVERSED")

    return await f.once(
        db, ctx, f"funds.source.reverse:{id}", key, {"reason": explanation}, execute
    )
