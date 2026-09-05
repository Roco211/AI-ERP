from decimal import Decimal, localcontext
from uuid import uuid4

from sqlalchemy import text

from forge_erp.core.errors import Problem
from forge_erp.modules.audit.service import record_mutation
from forge_erp.modules.catalog.domain.values import ConversionSnapshot
from forge_erp.modules.inventory.application.engine import InventoryEngine
from forge_erp.modules.purchasing.application import orders, queries
from forge_erp.modules.purchasing.domain.values import line_amount, return_amount

PERMISSIONS = {"RECEIPT": "purchase.receive", "RETURN": "purchase.return"}


async def lock_documents(db, ctx, ids):
    for id in sorted(set(ids)):
        await db.execute(
            text(
                "SELECT id FROM forge.inventory_documents WHERE organization_id=:org "
                "AND id=:id FOR UPDATE"
            ),
            {"org": ctx.organization_id, "id": id},
        )


async def active_sources(db, ctx, order, lines):
    await orders.refs(
        db,
        ctx,
        order["supplier_id"],
        order["warehouse_id"],
        [x["product_id"] for x in lines],
        [x["unit_id"] for x in lines],
    )
    for line in sorted(lines, key=lambda x: (x["product_id"], x["unit_id"])):
        if not (
            await db.execute(
                text(
                    "SELECT id FROM forge.product_units WHERE organization_id=:org AND "
                    "product_id=:p AND unit_id=:u AND active FOR SHARE"
                ),
                {"org": ctx.organization_id, "p": line["product_id"], "u": line["unit_id"]},
            )
        ).first():
            raise Problem(409, "MISSING_UNIT_CONVERSION", "商品采购单位已停用或不可使用")


async def save(db, ctx, kind, body, key, id=None, expected=None):
    orders.require(ctx, PERMISSIONS[kind])

    async def execute():
        original = await queries.raw_document(db, ctx, body.source_id) if kind == "RETURN" else None
        if original and original["kind"] != "RECEIPT":
            raise Problem(409, "INVALID_SOURCE", "退货必须引用原收货")
        order_id = original["order_id"] if original else body.source_id
        order = await orders.load(db, ctx, order_id, True)
        await lock_documents(db, ctx, ([id] if id else []) + ([original["id"]] if original else []))
        before = await queries.raw_document(db, ctx, id) if id else None
        if before:
            orders.version(before, expected, {"DRAFT"})
            if (
                before["order_id"] != order_id
                or before["kind"] != kind
                or before["original_document_id"] != (original["id"] if original else None)
            ):
                raise Problem(409, "INVALID_SOURCE", "不能更换单据来源")
        if kind == "RECEIPT" and order["status"] != "CONFIRMED":
            raise Problem(409, "INVALID_DOCUMENT_STATE", "仅已确认且未关闭的订单可收货")
        if original:
            original = await queries.raw_document(db, ctx, original["id"])
            if original["status"] != "POSTED":
                raise Problem(409, "INVALID_SOURCE", "原收货必须已过账且未冲销")
        if not body.reason.strip():
            raise Problem(422, "REASON_REQUIRED", "请填写单据原因")
        sources = {
            x["id"]: x
            for x in (
                await queries.raw_document_lines(db, ctx, original["id"])
                if original
                else await orders.raw_lines(db, ctx, order_id)
            )
        }
        captured = []
        for item in body.lines:
            source = sources.get(item.source_line_id)
            if source is None:
                raise Problem(404, "NOT_FOUND", "来源行不存在或不属于当前订单/收货")
            price = source["unit_price"] if item.unit_price is None else item.unit_price
            if original and price != source["unit_price"]:
                raise Problem(409, "RETURN_PRICE_FIXED", "退货参考单价必须沿用原收货价")
            amount = line_amount(item.qty, price)
            if original:
                amount = return_amount(
                    item.qty,
                    price,
                    source["qty"] - source["returned_qty"],
                    source["amount"] - source["returned_amount"],
                )
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
                    "product_label": source["product_label"],
                    "unit_label": source["unit_label"],
                    "unit_price": price,
                    "amount": amount,
                    "order_line_id": source["order_line_id"] if original else source["id"],
                    "receipt_line_id": source["id"] if original else None,
                }
            )
        await active_sources(db, ctx, order, captured)
        previous = await queries.raw_document_lines(db, ctx, id) if id else []
        doc_id = id or uuid4()
        params = {
            "org": ctx.organization_id,
            "id": doc_id,
            "type": "PURCHASE_" + kind,
            "kind": kind,
            "order": order_id,
            "original": original["id"] if original else None,
            "number": ("PR-" if kind == "RECEIPT" else "RT-") + uuid4().hex[:16].upper(),
            "reason": body.reason,
            "wh": order["warehouse_id"],
            "actor": ctx.user_id,
        }
        if id:
            await db.execute(
                text(
                    "DELETE FROM forge.purchase_document_lines WHERE organization_id=:org"
                    " AND document_id=:id"
                ),
                params,
            )
            await db.execute(
                text(
                    "DELETE FROM forge.inventory_document_lines WHERE "
                    "organization_id=:org AND document_id=:id"
                ),
                params,
            )
            await db.execute(
                text(
                    "UPDATE forge.inventory_documents SET "
                    "reason=:reason,version=version+1 WHERE organization_id=:org AND "
                    "id=:id"
                ),
                params,
            )
        else:
            await db.execute(
                text(
                    "INSERT INTO "
                    "forge.inventory_documents(id,organization_id,number,type,reason,warehouse_id,created_by)"
                    " VALUES(:id,:org,:number,:type,:reason,:wh,:actor)"
                ),
                params,
            )
            await db.execute(
                text(
                    "INSERT INTO "
                    "forge.purchase_documents(id,organization_id,order_id,kind,original_document_id)"
                    " VALUES(:id,:org,:order,:kind,:original)"
                ),
                params,
            )
        for n, line in enumerate(captured, 1):
            values = line | {
                "org": ctx.organization_id,
                "document_id": doc_id,
                "order_id": order_id,
                "n": n,
                "direction": "IN" if kind == "RECEIPT" else "OUT",
            }
            await db.execute(
                text(
                    "INSERT INTO "
                    "forge.inventory_document_lines(id,organization_id,document_id,line_no,product_id,unit_id,product_label,unit_label,qty,unit_to_base_factor,base_qty,conversion_version,direction)"
                    " "
                    "VALUES(:id,:org,:document_id,:n,:product_id,:unit_id,:product_label,:unit_label,:qty,:unit_to_base_factor,:base_qty,:conversion_version,:direction)"
                ),
                values,
            )
            await db.execute(
                text(
                    "INSERT INTO "
                    "forge.purchase_document_lines(id,organization_id,document_id,order_id,order_line_id,receipt_line_id,unit_price,amount)"
                    " "
                    "VALUES(:id,:org,:document_id,:order_id,:order_line_id,:receipt_line_id,:unit_price,:amount)"
                ),
                values,
            )
        result = await queries.raw_document(db, ctx, doc_id)
        await record_mutation(
            db,
            ctx,
            "purchase.document.update" if id else "purchase.document.create",
            "purchase_document",
            doc_id,
            {"document": before, "lines": previous} if before else None,
            {"version": result["version"], "document": result, "lines": captured},
        )
        return orders.receipt(ctx, result)

    return await orders.inventory_once(
        db,
        ctx,
        f"purchase.{kind}.save:{id or 'new'}",
        key,
        body.model_dump(mode="json") | {"expected_version": expected},
        execute,
    )


async def transition(db, ctx, id, expected, key, action, reason=None):
    initial = await queries.raw_document(db, ctx, id)
    permission = PERMISSIONS[initial["kind"]]
    orders.require(ctx, permission)
    if action == "reverse":
        ctx.require("purchase.reverse")

    async def execute():
        order = await orders.load(db, ctx, initial["order_id"], True)
        await lock_documents(
            db,
            ctx,
            [id] + ([initial["original_document_id"]] if initial["original_document_id"] else []),
        )
        doc = await queries.raw_document(db, ctx, id)
        orders.version(doc, expected, {"POSTED"} if action == "reverse" else {"DRAFT"})
        lines = await queries.raw_document_lines(db, ctx, id)
        if action != "reverse":
            await active_sources(db, ctx, order, lines)
        engine = InventoryEngine(db, ctx, permission)
        await engine.lock(
            [(doc["warehouse_id"], x["product_id"]) for x in lines], historical=action == "reverse"
        )
        if action == "reverse":
            if not reason or not reason.strip():
                raise Problem(422, "REASON_REQUIRED", "请填写冲销原因")
            if doc["kind"] == "RECEIPT" and any(x["returned_qty"] for x in lines):
                raise Problem(409, "REVERSAL_DEPENDENCY_CONFLICT", "原收货已有有效退货，不能冲销")
            movements = [
                dict(x)
                for x in (
                    await db.execute(
                        text(
                            "SELECT * FROM forge.inventory_movements WHERE organization_id=:org "
                            "AND document_id=:id AND kind<>'REVERSE' ORDER BY "
                            "warehouse_id,product_id,sequence DESC"
                        ),
                        {"org": ctx.organization_id, "id": id},
                    )
                ).mappings()
            ]
            for m in movements:
                if (
                    engine.rows[(m["warehouse_id"], m["product_id"])]["movement_sequence"]
                    != m["sequence"]
                ):
                    raise Problem(409, "REVERSAL_DEPENDENCY_CONFLICT", "已有后续库存变动，不能冲销")
            rid = (
                await db.execute(
                    text(
                        "INSERT INTO "
                        "forge.inventory_reversals(organization_id,document_id,reason,actor_id,request_id)"
                        " VALUES(:org,:id,:reason,:actor,:request) RETURNING id"
                    ),
                    {
                        "org": ctx.organization_id,
                        "id": id,
                        "reason": reason,
                        "actor": ctx.user_id,
                        "request": ctx.request_id,
                    },
                )
            ).scalar_one()
            for m in movements:
                await engine.reverse(m, rid)
            await db.execute(
                text(
                    "UPDATE forge.inventory_documents SET "
                    "status='REVERSED',reversed_at=now(),reversal_id=:rid,version=version+1"
                    " WHERE organization_id=:org AND id=:id"
                ),
                {"org": ctx.organization_id, "id": id, "rid": rid},
            )
        else:
            if doc["kind"] == "RECEIPT":
                if order["status"] != "CONFIRMED":
                    raise Problem(409, "INVALID_DOCUMENT_STATE", "订单已关闭或取消，不能收货")
                counts = await orders.quantities(db, ctx, order["id"])
                ordered = {x["id"]: x for x in await orders.raw_lines(db, ctx, order["id"])}
                for line in lines:
                    received = counts.get(line["order_line_id"], {}).get("received", Decimal(0))
                    if received + line["base_qty"] > ordered[line["order_line_id"]]["base_qty"]:
                        raise Problem(409, "OVER_RECEIPT", "累计实收超过采购订单数量")
            else:
                original = await queries.raw_document(db, ctx, doc["original_document_id"])
                if original["status"] != "POSTED":
                    raise Problem(409, "INVALID_SOURCE", "原收货已冲销或不可退货")
                sources = {
                    x["id"]: x for x in await queries.raw_document_lines(db, ctx, original["id"])
                }
                for line in lines:
                    source = sources[line["receipt_line_id"]]
                    amount = return_amount(
                        line["qty"],
                        source["unit_price"],
                        source["qty"] - source["returned_qty"],
                        source["amount"] - source["returned_amount"],
                    )
                    if amount != line["amount"]:
                        raise Problem(
                            409, "RETURN_QUOTE_CHANGED", "可退金额已变化，请重新保存草稿核对"
                        )
            for line in lines:
                with localcontext() as dec:
                    dec.prec = 50
                    await engine.change(
                        (doc["warehouse_id"], line["product_id"]),
                        line["id"],
                        id,
                        "RECEIVE" if doc["kind"] == "RECEIPT" else "ISSUE",
                        line["base_qty"],
                        value=line["amount"] if doc["kind"] == "RECEIPT" else None,
                    )
            await db.execute(
                text(
                    "UPDATE forge.inventory_documents SET "
                    "status='POSTED',posted_at=now(),version=version+1 WHERE "
                    "organization_id=:org AND id=:id"
                ),
                {"org": ctx.organization_id, "id": id},
            )
        result = await queries.raw_document(db, ctx, id)
        await record_mutation(
            db,
            ctx,
            "purchase.document." + action,
            "purchase_document",
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
        f"purchase.document.{action}:{id}",
        key,
        {"version": expected, "reason": reason},
        execute,
    )
