"""Sales shipment commands; the caller owns one function-scoped transaction."""

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
from forge_erp.modules.sales.application import orders, queries
from forge_erp.modules.sales.domain.values import line_amount


async def reservation_sources(db, ctx, order_id):
    return {
        row["order_line_id"]: dict(row)
        for row in (
            await db.execute(
                text("""SELECT l.id,l.document_id,sl.order_line_id
                    FROM forge.sales_documents sd
                    JOIN forge.inventory_documents d
                      ON (d.organization_id,d.id)=(sd.organization_id,sd.id)
                    JOIN forge.sales_document_lines sl
                      ON (sl.organization_id,sl.document_id)=(sd.organization_id,sd.id)
                    JOIN forge.inventory_document_lines l
                      ON (l.organization_id,l.id)=(sl.organization_id,sl.id)
                    WHERE sd.organization_id=:org AND sd.order_id=:order
                      AND sd.kind='RESERVATION' AND d.status='POSTED'"""),
                {"org": ctx.organization_id, "order": order_id},
            )
        ).mappings()
    }


async def lock_documents(db, ctx, ids):
    for id in sorted(set(ids)):
        await db.execute(
            text(
                "SELECT id FROM forge.inventory_documents "
                "WHERE organization_id=:org AND id=:id FOR UPDATE"
            ),
            {"org": ctx.organization_id, "id": id},
        )


async def active_sources(db, ctx, order, lines):
    await orders.refs(
        db,
        ctx,
        order["customer_id"],
        order["warehouse_id"],
        [x["product_id"] for x in lines],
        [x["unit_id"] for x in lines],
    )
    for line in sorted(lines, key=lambda x: (x["product_id"], x["unit_id"])):
        # A changed factor does not rewrite the confirmed order's frozen conversion.
        await orders.conversion(db, ctx, line["product_id"], line["unit_id"])


def confirmed(order):
    if order["status"] != "CONFIRMED":
        raise Problem(409, "INVALID_DOCUMENT_STATE", "仅已确认且未关闭的销售订单可出库")


async def save(db, ctx, body, key, id=None, expected=None):
    if id:
        current = await queries.raw_document(db, ctx, id)
        if current["kind"] == "RETURN":
            from forge_erp.modules.sales.application import returns

            return await returns.save(db, ctx, body, key, id, expected)
    ctx.require("sales.ship")

    async def execute():
        initial = await queries.raw_document(db, ctx, id) if id else None
        if initial and initial["order_id"] != body.source_id:
            raise Problem(409, "INVALID_SOURCE", "不能更换出库单来源订单")
        order = await orders.load(db, ctx, body.source_id, True)
        confirmed(order)
        sources = await reservation_sources(db, ctx, order["id"])
        await lock_documents(
            db, ctx, ([id] if id else []) + [x["document_id"] for x in sources.values()]
        )
        before = await queries.raw_document(db, ctx, id) if id else None
        if before:
            orders.version(before, expected, {"DRAFT"})
        if not body.reason.strip():
            raise Problem(422, "REASON_REQUIRED", "请填写出库原因")
        ordered = {x["id"]: x for x in await orders.raw_lines(db, ctx, order["id"])}
        captured = []
        for item in body.lines:
            source = ordered.get(item.source_line_id)
            if source is None:
                raise Problem(404, "NOT_FOUND", "来源行不存在或不属于当前销售订单")
            reservation = sources.get(source["id"])
            if reservation is None:
                raise Problem(409, "INVALID_SOURCE", "订单占用来源不完整，请核对")
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
                    "order_line_id": source["id"],
                    "reservation_source_line_id": reservation["id"],
                    "product_label": source["product_label"],
                    "unit_label": source["unit_label"],
                    "unit_price": source["unit_price"],
                    "amount": line_amount(item.qty, source["unit_price"]),
                }
            )
        with localcontext() as dec:
            dec.prec = 50
            exact(sum((x["amount"] for x in captured), Decimal(0)), 4)
        await active_sources(db, ctx, order, captured)
        previous = await queries.raw_document_lines(db, ctx, id) if id else []
        doc_id = id or uuid4()
        params = {
            "org": ctx.organization_id,
            "id": doc_id,
            "order": order["id"],
            "number": "SS-" + uuid4().hex[:16].upper(),
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
                text(
                    "INSERT INTO forge.inventory_documents "
                    "(id,organization_id,number,type,reason,warehouse_id,created_by) "
                    "VALUES(:id,:org,:number,'SALES_SHIPMENT',:reason,:wh,:actor)"
                ),
                params,
            )
            await db.execute(
                text(
                    "INSERT INTO forge.sales_documents "
                    "(id,organization_id,order_id,kind) VALUES(:id,:org,:order,'SHIPMENT')"
                ),
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
                 qty,unit_to_base_factor,base_qty,conversion_version,direction,
                 reservation_source_line_id)
                VALUES(:id,:org,:doc,:n,:product_id,:unit_id,:product_label,:unit_label,:qty,
                 :unit_to_base_factor,:base_qty,:conversion_version,'OUT',:reservation_source_line_id)
                """),
                values,
            )
            await db.execute(
                text("""INSERT INTO forge.sales_document_lines
                (id,organization_id,document_id,order_id,order_line_id,unit_price,amount)
                VALUES(:id,:org,:doc,:order,:order_line_id,:unit_price,:amount)"""),
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
        f"sales.shipment.save:{id or 'new'}",
        key,
        body.model_dump(mode="json") | {"expected_version": expected},
        execute,
    )


async def post(db, ctx, id, expected, key):
    current = await queries.raw_document(db, ctx, id)
    if current["kind"] == "RETURN":
        from forge_erp.modules.sales.application import returns

        return await returns.post(db, ctx, id, expected, key)
    ctx.require("sales.ship")

    async def execute():
        await cutover(db, ctx)
        initial = await queries.raw_document(db, ctx, id)
        order = await orders.load(db, ctx, initial["order_id"], True)
        sources = await reservation_sources(db, ctx, order["id"])
        await lock_documents(db, ctx, [id] + [x["document_id"] for x in sources.values()])
        doc = await queries.raw_document(db, ctx, id)
        orders.version(doc, expected, {"DRAFT"})
        confirmed(order)
        lines = await queries.raw_document_lines(db, ctx, id)
        if not lines:
            raise Problem(409, "INVALID_SOURCE", "出库单没有有效来源行")
        await active_sources(db, ctx, order, lines)
        ordered = {x["id"]: x for x in await orders.raw_lines(db, ctx, order["id"])}
        counts = await orders.quantities(db, ctx, order["id"])
        # Order lock serializes distinct shipments and closure, including drafts created earlier.
        for line in lines:
            origin = ordered.get(line["order_line_id"])
            reservation_source = sources.get(line["order_line_id"])
            if (
                origin is None
                or reservation_source is None
                or (line["reservation_source_line_id"] != reservation_source["id"])
            ):
                raise Problem(409, "INVALID_SOURCE", "出库与本订单占用来源不一致")
            for field in (
                "product_id",
                "unit_id",
                "unit_to_base_factor",
                "conversion_version",
                "unit_price",
            ):
                if line[field] != origin[field]:
                    raise Problem(409, "INVALID_SOURCE", "出库快照与确认订单不一致")
            if line["amount"] != line_amount(line["qty"], origin["unit_price"]):
                raise Problem(409, "INVALID_SOURCE", "出库金额与确认订单不一致")
            shipped = counts.get(line["order_line_id"], {}).get("shipped", Decimal(0))
            if shipped + line["base_qty"] > origin["base_qty"]:
                raise Problem(409, "OVER_SHIPMENT", "累计实发超过销售订单数量")
        engine = InventoryEngine(db, ctx, "sales.ship")
        await engine.lock([(doc["warehouse_id"], x["product_id"]) for x in lines])
        for line in sorted(lines, key=lambda x: x["product_id"]):
            reservation = (
                await db.execute(
                    text("""SELECT id FROM forge.inventory_reservations
                WHERE organization_id=:org AND line_id=:line FOR UPDATE"""),
                    {"org": ctx.organization_id, "line": line["reservation_source_line_id"]},
                )
            ).scalar_one_or_none()
            if reservation is None:
                raise Problem(409, "INSUFFICIENT_RESERVATION", "本订单库存占用不存在")
            await engine.change(
                (doc["warehouse_id"], line["product_id"]),
                line["id"],
                id,
                "ISSUE",
                line["base_qty"],
                reservation_id=reservation,
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
        db, ctx, f"sales.document.post:{id}", key, {"expected_version": expected}, execute
    )
