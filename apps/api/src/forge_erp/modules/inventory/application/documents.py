from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.context import RuntimeContext
from forge_erp.core.errors import Problem
from forge_erp.core.idempotency import execute_once
from forge_erp.modules.audit.service import record_mutation
from forge_erp.modules.catalog.domain.values import ConversionSnapshot
from forge_erp.modules.inventory.application.engine import InventoryEngine
from forge_erp.modules.inventory.domain.schemas import DraftInput as InventoryDraftInput
from forge_erp.modules.inventory.domain.values import ZERO

PERMISSIONS = {
    "OPENING": "inventory.opening",
    "ADJUSTMENT": "inventory.adjust",
    "TRANSFER": "inventory.transfer",
    "STOCKTAKE": "inventory.stocktake",
}


async def load(db: AsyncSession, ctx: RuntimeContext, id: UUID, *, lock: bool = False) -> dict:
    row = (
        (
            await db.execute(
                text(
                    "SELECT * FROM forge.inventory_documents "
                    "WHERE organization_id=:org AND id=:id" + (" FOR UPDATE" if lock else "")
                ),
                {"org": ctx.organization_id, "id": id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "NOT_FOUND", "单据不存在或无权访问")
    return dict(row)


async def lines(db: AsyncSession, ctx: RuntimeContext, id: UUID) -> list[dict]:
    return [
        dict(r)
        for r in (
            await db.execute(
                text(
                    "SELECT * FROM forge.inventory_document_lines "
                    "WHERE organization_id=:org AND document_id=:id ORDER BY line_no"
                ),
                {"org": ctx.organization_id, "id": id},
            )
        ).mappings()
    ]


def require(ctx: RuntimeContext, kind: str, action: str):
    ctx.require(PERMISSIONS[kind])
    # Inventory documents carry estimates and require informed valuation decisions.
    ctx.require("product.cost.read")
    if action == "reverse":
        ctx.require("inventory.reverse")


def check_version(doc: dict, version: int, status: str):
    if doc["status"] != status:
        raise Problem(409, "INVALID_DOCUMENT_STATE", "单据状态不允许此操作")
    if doc["version"] != version:
        raise Problem(409, "DOCUMENT_VERSION_CONFLICT", "单据已变更，请刷新并重新确认")


async def snapshots(
    db: AsyncSession,
    ctx: RuntimeContext,
    body: InventoryDraftInput,
    kind: str,
    engine: InventoryEngine,
    previous: list[dict] | None = None,
) -> list[dict]:
    if (kind == "TRANSFER") != (body.target_warehouse_id is not None):
        raise Problem(422, "INVALID_REFERENCE", "调拨必须指定不同的目标仓库")
    # Lock all unit rows before conversion rows, using stable IDs.
    for unit in sorted({line.unit_id for line in body.lines}):
        row = (
            await db.execute(
                text(
                    "SELECT id FROM forge.units WHERE organization_id=:org "
                    "AND id=:id AND active FOR SHARE"
                ),
                {"org": ctx.organization_id, "id": unit},
            )
        ).first()
        if row is None:
            raise Problem(409, "INVALID_REFERENCE", "单位已停用或不可使用")
    result = []
    old = {x["product_id"]: x for x in (previous or [])}
    for line in sorted(body.lines, key=lambda x: (x.product_id, x.unit_id)):
        ref = (
            (
                await db.execute(
                    text(
                        "SELECT pu.*,p.sku,p.name,u.name AS unit_name "
                        "FROM forge.product_units pu JOIN forge.products p "
                        "ON (p.organization_id,p.id)=(pu.organization_id,pu.product_id) "
                        "JOIN forge.units u ON (u.organization_id,"
                        "u.id)=(pu.organization_id,pu.unit_id) "
                        "WHERE pu.organization_id=:org AND "
                        "pu.product_id=:product AND pu.unit_id=:unit "
                        "AND pu.active FOR SHARE OF pu"
                    ),
                    {"org": ctx.organization_id, "product": line.product_id, "unit": line.unit_id},
                )
            )
            .mappings()
            .first()
        )
        if ref is None:
            raise Problem(409, "MISSING_UNIT_CONVERSION", "商品单位换算不存在或已停用")
        if kind != "STOCKTAKE" and line.qty == 0:
            raise Problem(422, "INVALID_NUMBER", "单据数量必须大于零")
        if kind != "ADJUSTMENT" and line.direction != "IN":
            raise Problem(422, "INVALID_OPERATION", "该类单据不接受调整方向")
        snap = ConversionSnapshot.capture(
            line.product_id, line.unit_id, line.qty, ref["unit_to_base_factor"], ref["version"]
        )
        baseline = old.get(line.product_id)
        current = engine.rows[(body.warehouse_id, line.product_id)]
        result.append(
            {
                "id": uuid4(),
                "product_id": line.product_id,
                "unit_id": line.unit_id,
                "qty": snap.qty,
                "factor": snap.unit_to_base_factor,
                "base_qty": snap.base_qty,
                "conversion_version": snap.conversion_version,
                "direction": line.direction,
                "input_unit_cost": line.input_unit_cost,
                "product_label": ref["sku"] + " · " + ref["name"],
                "unit_label": ref["unit_name"],
                "baseline_qty": baseline["baseline_qty"] if baseline else current["on_hand_qty"],
                "baseline_version": baseline["baseline_version"]
                if baseline
                else current["version"],
            }
        )
    return result


async def save_draft(
    db: AsyncSession,
    ctx: RuntimeContext,
    kind: str,
    body: InventoryDraftInput,
    key: str,
    id: UUID | None = None,
    version: int | None = None,
) -> dict:
    require(ctx, kind, "write")

    async def execute():
        previous = None
        if id:
            previous = await load(db, ctx, id, lock=True)
            if previous["type"] != kind:
                raise Problem(409, "INVALID_DOCUMENT_STATE", "不能修改单据类型")
            check_version(previous, version or 0, "DRAFT")
        e = InventoryEngine(db, ctx, PERMISSIONS[kind])
        keys = [(body.warehouse_id, x.product_id) for x in body.lines]
        if body.target_warehouse_id:
            keys += [(body.target_warehouse_id, x.product_id) for x in body.lines]
        await e.lock(keys)
        previous_lines = await lines(db, ctx, id) if id else []
        old_lines = (
            previous_lines if previous and previous["warehouse_id"] == body.warehouse_id else None
        )
        captured = await snapshots(db, ctx, body, kind, e, old_lines)
        doc_id = id or uuid4()
        params = {
            "org": ctx.organization_id,
            "id": doc_id,
            "type": kind,
            "reason": body.reason,
            "wh": body.warehouse_id,
            "target": body.target_warehouse_id,
            "actor": ctx.user_id,
            "number": kind[:3] + "-" + uuid4().hex[:16].upper(),
        }
        if id:
            await db.execute(
                text(
                    "DELETE FROM forge.inventory_document_lines "
                    "WHERE organization_id=:org AND document_id=:id"
                ),
                params,
            )
            doc = dict(
                (
                    await db.execute(
                        text(
                            "UPDATE forge.inventory_documents SET reason=:reason,"
                            "warehouse_id=:wh,target_warehouse_id=:target,version=version+1 "
                            "WHERE organization_id=:org AND id=:id RETURNING *"
                        ),
                        params,
                    )
                )
                .mappings()
                .one()
            )
        else:
            doc = dict(
                (
                    await db.execute(
                        text(
                            "INSERT INTO forge.inventory_documents "
                            "(id,organization_id,number,type,reason,"
                            "warehouse_id,target_warehouse_id,created_by) "
                            "VALUES (:id,:org,:number,:type,:reason,:wh,:target,:actor) RETURNING *"
                        ),
                        params,
                    )
                )
                .mappings()
                .one()
            )
        for i, line in enumerate(captured, 1):
            await db.execute(
                text(
                    "INSERT INTO forge.inventory_document_lines "
                    "(id,organization_id,document_id,line_no,"
                    "product_id,unit_id,product_label,unit_label,qty,"
                    "unit_to_base_factor,base_qty,conversion_version,"
                    "direction,input_unit_cost,baseline_qty,baseline_version) "
                    "VALUES (:id,:org,:doc,:number,:product_id,"
                    ":unit_id,:product_label,:unit_label,:qty,"
                    ":factor,:base_qty,:conversion_version,:direction,"
                    ":input_unit_cost,:baseline_qty,:baseline_version)"
                ),
                line | {"org": ctx.organization_id, "doc": doc_id, "number": i},
            )
        action = "update" if id else "create"
        await record_mutation(
            db,
            ctx,
            "inventory.document." + action,
            "inventory_document",
            doc_id,
            {"document": previous, "lines": previous_lines} if previous else None,
            {"version": doc["version"], "document": doc, "lines": await lines(db, ctx, doc_id)},
        )
        return receipt(ctx, doc)

    return await inventory_once(
        db,
        ctx,
        f"inventory.{kind}.draft:{id or 'new'}",
        key,
        body.model_dump(mode="json") | {"expected_version": version},
        execute,
    )


def receipt(ctx: RuntimeContext, doc: dict) -> dict:
    return {
        "id": str(doc["id"]),
        "status": doc["status"],
        "version": doc["version"],
        "request_id": ctx.request_id,
    }


async def transition(
    db: AsyncSession,
    ctx: RuntimeContext,
    id: UUID,
    version: int,
    key: str,
    action: str,
    reason: str | None = None,
) -> dict:
    initial = await load(db, ctx, id)
    require(ctx, initial["type"], action)

    async def execute():
        doc = await load(db, ctx, id, lock=True)
        check_version(doc, version, "POSTED" if action == "reverse" else "DRAFT")
        detail = await lines(db, ctx, id)
        keys = [(doc["warehouse_id"], x["product_id"]) for x in detail]
        if doc["target_warehouse_id"]:
            keys += [(doc["target_warehouse_id"], x["product_id"]) for x in detail]
        e = InventoryEngine(db, ctx, PERMISSIONS[doc["type"]])
        await e.lock(keys, historical=action == "reverse")
        if action == "reverse":
            if not reason or not reason.strip():
                raise Problem(422, "REASON_REQUIRED", "请填写冲销原因")
            movements = [
                dict(x)
                for x in (
                    await db.execute(
                        text(
                            "SELECT * FROM forge.inventory_movements "
                            "WHERE organization_id=:org AND "
                            "document_id=:id AND kind<>'REVERSE' "
                            "ORDER BY warehouse_id,product_id,sequence DESC"
                        ),
                        {"org": ctx.organization_id, "id": id},
                    )
                ).mappings()
            ]
            # v0.6 documents create at most one movement per inventory key.
            for m in movements:
                if (
                    e.rows[(m["warehouse_id"], m["product_id"])]["movement_sequence"]
                    != m["sequence"]
                ):
                    raise Problem(
                        409, "REVERSAL_DEPENDENCY_CONFLICT", "该单据已有后续库存变动，不能冲销"
                    )
            reversal = (
                await db.execute(
                    text(
                        "INSERT INTO forge.inventory_reversals "
                        "(organization_id,document_id,reason,actor_id,"
                        "request_id) VALUES (:org,:id,:reason,:actor,:rid) RETURNING id"
                    ),
                    {
                        "org": ctx.organization_id,
                        "id": id,
                        "reason": reason,
                        "actor": ctx.user_id,
                        "rid": ctx.request_id,
                    },
                )
            ).scalar_one()
            for m in movements:
                await e.reverse(m, reversal)
            await db.execute(
                text(
                    "UPDATE forge.inventory_documents SET status='REVERSED',version=version+1,"
                    "reversal_id=:reversal,reversed_at=now() WHERE organization_id=:org AND id=:id"
                ),
                {"org": ctx.organization_id, "id": id, "reversal": reversal},
            )
        elif action == "refresh":
            if doc["type"] != "STOCKTAKE":
                raise Problem(409, "INVALID_DOCUMENT_STATE", "仅盘点草稿可刷新基准")
            for line in detail:
                current = e.rows[(doc["warehouse_id"], line["product_id"])]
                await db.execute(
                    text(
                        "UPDATE forge.inventory_document_lines SET baseline_qty=:qty,"
                        "baseline_version=:version WHERE organization_id=:org AND id=:id"
                    ),
                    {
                        "org": ctx.organization_id,
                        "id": line["id"],
                        "qty": current["on_hand_qty"],
                        "version": current["version"],
                    },
                )
            await db.execute(
                text(
                    "UPDATE forge.inventory_documents SET version=version+1 "
                    "WHERE organization_id=:org AND id=:id"
                ),
                {"org": ctx.organization_id, "id": id},
            )
        elif action == "post":
            # Shared unit/conversion locks prevent concurrent edits until commit.
            for unit in sorted({x["unit_id"] for x in detail}):
                found = (
                    await db.execute(
                        text(
                            "SELECT id FROM forge.units WHERE organization_id=:org "
                            "AND id=:id AND active FOR SHARE"
                        ),
                        {"org": ctx.organization_id, "id": unit},
                    )
                ).first()
                if not found:
                    raise Problem(409, "INVALID_REFERENCE", "单位已停用")
            for line in sorted(detail, key=lambda x: (x["product_id"], x["unit_id"])):
                conversion = (
                    await db.execute(
                        text(
                            "SELECT version FROM forge.product_units WHERE "
                            "organization_id=:org AND "
                            "product_id=:product AND unit_id=:unit AND active FOR SHARE"
                        ),
                        {
                            "org": ctx.organization_id,
                            "product": line["product_id"],
                            "unit": line["unit_id"],
                        },
                    )
                ).first()
                if not conversion or conversion.version != line["conversion_version"]:
                    raise Problem(
                        409, "UNIT_CONVERSION_CHANGED", "换算已变化，请编辑草稿重新确认数量"
                    )
                stock_key = (doc["warehouse_id"], line["product_id"])
                qty = line["base_qty"]
                if doc["type"] == "STOCKTAKE":
                    if e.rows[stock_key]["version"] != line["baseline_version"]:
                        raise Problem(
                            409, "STOCKTAKE_STALE", "盘点期间库存已变化，请刷新基准并复核实盘"
                        )
                    qty -= line["baseline_qty"]
                elif doc["type"] == "ADJUSTMENT" and line["direction"] == "OUT":
                    qty = -qty
                if doc["type"] == "OPENING":
                    existing = (
                        await db.execute(
                            text(
                                "SELECT 1 FROM forge.inventory_movements m "
                                "JOIN forge.inventory_documents d ON "
                                "(d.organization_id,d.id)=(m.organization_id,m.document_id) "
                                "WHERE m.organization_id=:org AND "
                                "m.warehouse_id=:wh AND m.product_id=:product "
                                "AND (d.type<>'OPENING' OR d.status<>'REVERSED') LIMIT 1"
                            ),
                            {
                                "org": ctx.organization_id,
                                "wh": stock_key[0],
                                "product": stock_key[1],
                            },
                        )
                    ).first()
                    if existing or e.state(stock_key).qty or e.state(stock_key).reserved:
                        raise Problem(409, "OPENING_NOT_ALLOWED", "已有库存业务，不能覆盖期初")
                if doc["type"] == "TRANSFER":
                    outgoing = await e.change(stock_key, line["id"], id, "TRANSFER_OUT", qty)
                    await e.change(
                        (doc["target_warehouse_id"], line["product_id"]),
                        line["id"],
                        id,
                        "TRANSFER_IN",
                        qty,
                        value=-outgoing["value_delta"],
                    )
                elif qty > ZERO:
                    await e.change(
                        stock_key, line["id"], id, "RECEIVE", qty, cost=line["input_unit_cost"]
                    )
                elif qty < ZERO:
                    await e.change(stock_key, line["id"], id, "ISSUE", -qty)
            await db.execute(
                text(
                    "UPDATE forge.inventory_documents SET status='POSTED',posted_at=now(),"
                    "version=version+1 WHERE organization_id=:org AND id=:id"
                ),
                {"org": ctx.organization_id, "id": id},
            )
        else:
            raise ValueError("Unknown document action")
        updated = await load(db, ctx, id)
        await record_mutation(
            db,
            ctx,
            "inventory.document." + action,
            "inventory_document",
            id,
            {"status": doc["status"], "version": doc["version"]},
            {
                "status": updated["status"],
                "version": updated["version"],
                "reason": reason or doc["reason"],
            },
        )
        return receipt(ctx, updated)

    return await inventory_once(
        db,
        ctx,
        f"inventory.document.{action}:{id}",
        key,
        {"expected_version": version, "reason": reason},
        execute,
    )


async def inventory_once(db, ctx, operation, key, body, command):
    try:
        await db.execute(text("SET LOCAL lock_timeout = '5s'"))
        return await execute_once(db, ctx, operation, key, body, command)
    except ValidationError as exc:
        raise Problem(422, "INVALID_UNIT_QUANTITY", "单位换算后的数量超出精度或范围") from exc
    except DBAPIError as exc:
        code = getattr(exc.orig, "sqlstate", None)
        if code in {"40P01", "55P03"}:
            raise Problem(409, "INVENTORY_BUSY", "库存正由其他操作处理，请稍后重试") from exc
        if code in {"23505", "23503", "23514", "22003"}:
            raise Problem(409, "INVENTORY_CONFLICT", "库存约束或单据状态冲突，请刷新核对") from exc
        raise
