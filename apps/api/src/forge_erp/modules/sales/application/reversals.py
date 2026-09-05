"""Strict last-document correction, preserving original sales and inventory facts."""

from sqlalchemy import text

from forge_erp.core.errors import Problem
from forge_erp.modules.audit.service import record_mutation
from forge_erp.modules.inventory.application.engine import InventoryEngine
from forge_erp.modules.sales.application import documents, orders, queries


async def reverse(db, ctx, id, expected, key, reason):
    ctx.require("sales.reverse")
    initial = await queries.raw_document(db, ctx, id)
    permission = "sales.ship" if initial["kind"] == "SHIPMENT" else "sales.return"
    ctx.require(permission)

    async def execute():
        order = await orders.load(db, ctx, initial["order_id"], True)
        related = [id]
        if initial["original_document_id"]:
            related.append(initial["original_document_id"])
        else:
            sources = await documents.reservation_sources(db, ctx, order["id"])
            related.extend(x["document_id"] for x in sources.values())
        await documents.lock_documents(db, ctx, related)
        doc = await queries.raw_document(db, ctx, id)
        orders.version(doc, expected, {"POSTED"})
        if not reason or not reason.strip():
            raise Problem(422, "REASON_REQUIRED", "请填写冲销原因")
        lines = await queries.raw_document_lines(db, ctx, id)
        if doc["kind"] == "SHIPMENT":
            if order["status"] != "CONFIRMED":
                raise Problem(409, "INVALID_DOCUMENT_STATE", "订单已关闭或取消，不能冲销出库")
            if any(x["returned_qty"] for x in lines):
                raise Problem(409, "REVERSAL_DEPENDENCY_CONFLICT", "出库已有有效退货，不能冲销")
        engine = InventoryEngine(db, ctx, permission)
        await engine.lock([(doc["warehouse_id"], x["product_id"]) for x in lines], historical=True)
        movements = [
            dict(x)
            for x in (
                await db.execute(
                    text("""SELECT *
            FROM forge.inventory_movements WHERE organization_id=:org
              AND document_id=:id AND operation_id=:id AND kind=:kind
            ORDER BY warehouse_id,product_id,sequence DESC"""),
                    {
                        "org": ctx.organization_id,
                        "id": id,
                        "kind": "ISSUE" if doc["kind"] == "SHIPMENT" else "RECEIVE",
                    },
                )
            ).mappings()
        ]
        if (
            not lines
            or len(movements) != len(lines)
            or {x["line_id"] for x in movements} != {x["id"] for x in lines}
        ):
            raise Problem(409, "INVALID_SOURCE", "单据库存事实不完整，不能冲销")
        for m in movements:
            if (
                engine.rows[(m["warehouse_id"], m["product_id"])]["movement_sequence"]
                != m["sequence"]
            ):
                raise Problem(409, "REVERSAL_DEPENDENCY_CONFLICT", "已有后续库存变动，不能冲销")
        rid = (
            await db.execute(
                text("""INSERT INTO forge.inventory_reversals
          (organization_id,document_id,reason,actor_id,request_id)
          VALUES(:org,:id,:reason,:actor,:request) RETURNING id"""),
                {
                    "org": ctx.organization_id,
                    "id": id,
                    "reason": reason,
                    "actor": ctx.user_id,
                    "request": ctx.request_id,
                },
            )
        ).scalar_one()
        for movement in movements:
            await engine.reverse(movement, rid)
        await db.execute(
            text("""UPDATE forge.inventory_documents
          SET status='REVERSED',reversed_at=now(),reversal_id=:rid,version=version+1
          WHERE organization_id=:org AND id=:id"""),
            {"org": ctx.organization_id, "id": id, "rid": rid},
        )
        result = await queries.raw_document(db, ctx, id)
        await record_mutation(
            db,
            ctx,
            "sales.document.reverse",
            "sales_document",
            id,
            {"document": doc, "lines": lines},
            {
                "version": result["version"],
                "document": result,
                "reason": reason,
                "lines": await queries.raw_document_lines(db, ctx, id),
            },
        )
        return orders.receipt(ctx, result)

    return await orders.inventory_once(
        db,
        ctx,
        f"sales.document.reverse:{id}",
        key,
        {"expected_version": expected, "reason": reason},
        execute,
    )
