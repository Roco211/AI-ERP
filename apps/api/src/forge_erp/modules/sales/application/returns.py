"""Original-shipment returns; source amounts and costs are server-authoritative."""

from decimal import Decimal, localcontext
from uuid import uuid4

from sqlalchemy import text

from forge_erp.core.errors import Problem
from forge_erp.modules.audit.service import record_mutation
from forge_erp.modules.catalog.domain.values import ConversionSnapshot
from forge_erp.modules.funds.application import integration as funds
from forge_erp.modules.funds.application.shared import cutover
from forge_erp.modules.inventory.application.engine import InventoryEngine
from forge_erp.modules.inventory.domain.values import exact
from forge_erp.modules.sales.application import documents, orders, queries
from forge_erp.modules.sales.domain.values import return_values


def original_valid(order, original):
    if original["kind"] != "SHIPMENT" or original["status"] != "POSTED":
        raise Problem(409, "INVALID_SOURCE", "退货必须引用已过账且未冲销的销售出库")
    if order["status"] not in {"CONFIRMED", "CLOSED"}:
        raise Problem(409, "INVALID_DOCUMENT_STATE", "销售订单状态不允许退货")


def allocation(qty, source):
    if source["actual_cost"] is None:
        raise Problem(409, "INVALID_SOURCE", "原出库成本事实不完整，请核对")
    return return_values(
        qty,
        source["unit_price"],
        source["qty"],
        source["amount"],
        source["actual_cost"],
        source["returned_qty"],
        source["returned_amount"],
        source["returned_cost"],
    )


async def save(db, ctx, body, key, id=None, expected=None):
    ctx.require("sales.return")

    async def execute():
        if id:
            existing = await queries.raw_document(db, ctx, id)
            if existing["kind"] != "RETURN" or existing["original_document_id"] != body.source_id:
                raise Problem(409, "INVALID_SOURCE", "不能更换退货来源出库单")
        original = await queries.raw_document(db, ctx, body.source_id)
        order = await orders.load(db, ctx, original["order_id"], True)
        await documents.lock_documents(db, ctx, [original["id"]] + ([id] if id else []))
        original = await queries.raw_document(db, ctx, body.source_id)
        original_valid(order, original)
        before = await queries.raw_document(db, ctx, id) if id else None
        if before:
            orders.version(before, expected, {"DRAFT"})
            if (
                before["kind"] != "RETURN"
                or before["order_id"] != order["id"]
                or before["original_document_id"] != original["id"]
            ):
                raise Problem(409, "INVALID_SOURCE", "不能更换退货来源出库单")
        if not body.reason.strip():
            raise Problem(422, "REASON_REQUIRED", "请填写退货原因")
        sources = {x["id"]: x for x in await queries.raw_document_lines(db, ctx, original["id"])}
        captured = []
        for item in body.lines:
            source = sources.get(item.source_line_id)
            if source is None:
                raise Problem(404, "NOT_FOUND", "来源行不存在或不属于原出库单")
            amount, cost = allocation(item.qty, source)
            snap = ConversionSnapshot.capture(
                source["product_id"],
                source["unit_id"],
                item.qty,
                source["unit_to_base_factor"],
                source["conversion_version"],
            )
            captured.append(
                snap.model_dump()
                | {
                    "id": uuid4(),
                    "order_line_id": source["order_line_id"],
                    "shipment_line_id": source["id"],
                    "original_line_id": source["id"],
                    "product_label": source["product_label"],
                    "unit_label": source["unit_label"],
                    "unit_price": source["unit_price"],
                    "amount": amount,
                    "return_cost": cost,
                }
            )
        with localcontext() as dec:
            dec.prec = 50
            exact(sum((x["amount"] for x in captured), Decimal(0)), 4)
            exact(sum((x["return_cost"] for x in captured), Decimal(0)), 4)
        await documents.active_sources(db, ctx, order, captured)
        previous = await queries.raw_document_lines(db, ctx, id) if id else []
        doc_id = id or uuid4()
        params = {
            "org": ctx.organization_id,
            "id": doc_id,
            "order": order["id"],
            "original": original["id"],
            "number": "SR-" + uuid4().hex[:16].upper(),
            "reason": body.reason,
            "wh": order["warehouse_id"],
            "actor": ctx.user_id,
        }
        if id:
            await db.execute(
                text(
                    "DELETE FROM forge.sales_document_lines "
                    "WHERE organization_id=:org AND document_id=:id"
                ),
                params,
            )
            await db.execute(
                text(
                    "DELETE FROM forge.inventory_document_lines "
                    "WHERE organization_id=:org AND document_id=:id"
                ),
                params,
            )
            await db.execute(
                text(
                    "UPDATE forge.inventory_documents SET reason=:reason,"
                    "version=version+1 WHERE organization_id=:org AND id=:id"
                ),
                params,
            )
        else:
            await db.execute(
                text("""INSERT INTO forge.inventory_documents
              (id,organization_id,number,type,reason,warehouse_id,created_by)
              VALUES(:id,:org,:number,'SALES_RETURN',:reason,:wh,:actor)"""),
                params,
            )
            await db.execute(
                text("""INSERT INTO forge.sales_documents
              (id,organization_id,order_id,kind,original_document_id)
              VALUES(:id,:org,:order,'RETURN',:original)"""),
                params,
            )
        for n, line in enumerate(captured, 1):
            values = line | {
                "org": ctx.organization_id,
                "doc": doc_id,
                "order": order["id"],
                "n": n,
            }
            await db.execute(
                text("""INSERT INTO forge.inventory_document_lines
              (id,organization_id,document_id,line_no,product_id,unit_id,product_label,unit_label,
               qty,unit_to_base_factor,base_qty,conversion_version,direction,original_line_id)
              VALUES(:id,:org,:doc,:n,:product_id,:unit_id,:product_label,:unit_label,:qty,
               :unit_to_base_factor,:base_qty,:conversion_version,'IN',:original_line_id)"""),
                values,
            )
            await db.execute(
                text("""INSERT INTO forge.sales_document_lines
              (id,organization_id,document_id,order_id,order_line_id,shipment_line_id,
               unit_price,amount,return_cost)
              VALUES(:id,:org,:doc,:order,:order_line_id,:shipment_line_id,
               :unit_price,:amount,:return_cost)"""),
                values,
            )
        result = await queries.raw_document(db, ctx, doc_id)
        await record_mutation(
            db,
            ctx,
            "sales.document.update" if id else "sales.document.create",
            "sales_document",
            doc_id,
            {"document": before, "lines": previous} if before else None,
            {"version": result["version"], "document": result, "lines": captured},
        )
        return orders.receipt(ctx, result)

    return await orders.inventory_once(
        db,
        ctx,
        f"sales.return.save:{id or 'new'}",
        key,
        body.model_dump(mode="json") | {"expected_version": expected},
        execute,
    )


async def post(db, ctx, id, expected, key):
    ctx.require("sales.return")

    async def execute():
        await cutover(db, ctx)
        initial = await queries.raw_document(db, ctx, id)
        order = await orders.load(db, ctx, initial["order_id"], True)
        if initial["kind"] != "RETURN":
            raise Problem(409, "INVALID_SOURCE", "退货命令仅适用于销售退货单")
        await documents.lock_documents(db, ctx, [id, initial["original_document_id"]])
        doc = await queries.raw_document(db, ctx, id)
        orders.version(doc, expected, {"DRAFT"})
        original = await queries.raw_document(db, ctx, doc["original_document_id"])
        original_valid(order, original)
        lines = await queries.raw_document_lines(db, ctx, id)
        if not lines:
            raise Problem(409, "INVALID_SOURCE", "退货单没有有效来源行")
        sources = {x["id"]: x for x in await queries.raw_document_lines(db, ctx, original["id"])}
        await documents.active_sources(db, ctx, order, lines)
        for line in lines:
            source = sources.get(line["shipment_line_id"])
            if source is None or line["original_line_id"] != source["id"]:
                raise Problem(409, "INVALID_SOURCE", "退货行与原出库来源不一致")
            for field in (
                "order_line_id",
                "product_id",
                "unit_id",
                "unit_to_base_factor",
                "conversion_version",
                "unit_price",
            ):
                if line[field] != source[field]:
                    raise Problem(409, "INVALID_SOURCE", "退货快照与原出库不一致")
            amount, cost = allocation(line["qty"], source)
            if amount != line["amount"] or cost != line["return_cost"]:
                raise Problem(409, "RETURN_QUOTE_CHANGED", "可退金额已变化，请重新保存草稿核对")
        engine = InventoryEngine(db, ctx, "sales.return")
        await engine.lock([(doc["warehouse_id"], x["product_id"]) for x in lines])
        for line in sorted(lines, key=lambda x: x["product_id"]):
            await engine.change(
                (doc["warehouse_id"], line["product_id"]),
                line["id"],
                id,
                "RECEIVE",
                line["base_qty"],
                value=line["return_cost"],
            )
        await db.execute(
            text(
                "UPDATE forge.inventory_documents SET status='POSTED',"
                "posted_at=now(),version=version+1 WHERE organization_id=:org AND id=:id"
            ),
            {"org": ctx.organization_id, "id": id},
        )
        await funds.post(db, ctx, "AR", id)
        result = await queries.raw_document(db, ctx, id)
        await record_mutation(
            db,
            ctx,
            "sales.document.post",
            "sales_document",
            id,
            {"document": doc, "lines": lines},
            {
                "version": result["version"],
                "document": result,
                "lines": await queries.raw_document_lines(db, ctx, id),
            },
        )
        return orders.receipt(ctx, result)

    return await orders.inventory_once(
        db,
        ctx,
        f"sales.document.post:{id}",
        key,
        {"expected_version": expected},
        execute,
    )
