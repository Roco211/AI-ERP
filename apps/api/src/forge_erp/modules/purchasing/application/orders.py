from decimal import Decimal, localcontext
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.context import RuntimeContext
from forge_erp.core.errors import Problem
from forge_erp.modules.audit.service import record_mutation
from forge_erp.modules.catalog.domain.values import ConversionSnapshot
from forge_erp.modules.inventory.application.documents import inventory_once as shared_once
from forge_erp.modules.inventory.domain.values import InventoryError, exact
from forge_erp.modules.purchasing.domain.schemas import PurchaseOrderInput
from forge_erp.modules.purchasing.domain.values import line_amount


async def load(db: AsyncSession, ctx: RuntimeContext, id: UUID, lock=False, *, share=False) -> dict:
    row = (
        (
            await db.execute(
                text(
                    "SELECT * FROM forge.purchase_orders WHERE organization_id=:org AND id=:id"
                    + (" FOR UPDATE" if lock else " FOR SHARE" if share else "")
                ),
                {"org": ctx.organization_id, "id": id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "NOT_FOUND", "采购订单不存在或无权访问")
    return dict(row)


def require(ctx: RuntimeContext, permission: str):
    ctx.require(permission)
    ctx.require("product.cost.read")


def receipt(ctx, row):
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "version": row["version"],
        "request_id": ctx.request_id,
    }


def version(row, expected, statuses):
    if row["status"] not in statuses:
        raise Problem(409, "INVALID_DOCUMENT_STATE", "单据状态不允许此操作")
    if row["version"] != expected:
        raise Problem(409, "DOCUMENT_VERSION_CONFLICT", "单据已变更，请刷新重核")


async def refs(db, ctx, supplier, warehouse, products, units):
    names = {}
    for table, ids in [
        ("suppliers", [supplier]),
        ("warehouses", [warehouse]),
        ("products", products),
        ("units", units),
    ]:
        for id in sorted(set(ids)):
            row = (
                (
                    await db.execute(
                        text(
                            f"SELECT * FROM forge.{table} "
                            "WHERE organization_id=:org AND id=:id AND active FOR SHARE"
                        ),
                        {"org": ctx.organization_id, "id": id},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise Problem(
                    409, "INVALID_REFERENCE", "供应商、仓库、商品或单位不存在、已停用或无权使用"
                )
            names[(table, id)] = dict(row)
    return names


async def raw_lines(db, ctx, id):
    return [
        dict(x)
        for x in (
            await db.execute(
                text(
                    "SELECT * FROM forge.purchase_order_lines WHERE organization_id=:org "
                    "AND order_id=:id ORDER BY line_no"
                ),
                {"org": ctx.organization_id, "id": id},
            )
        ).mappings()
    ]


async def quantities(db, ctx, id):
    return {
        r["order_line_id"]: dict(r)
        for r in (
            await db.execute(
                text("""SELECT pl.order_line_id,
      coalesce(sum(il.base_qty) FILTER(WHERE pd.kind='RECEIPT'),0) AS received,
      coalesce(sum(il.base_qty) FILTER(WHERE pd.kind='RETURN'),0) AS returned
      FROM forge.purchase_document_lines pl JOIN forge.purchase_documents pd
       ON (pd.organization_id,pd.id)=(pl.organization_id,pl.document_id)
      JOIN forge.inventory_document_lines il ON 
(il.organization_id,il.id)=(pl.organization_id,pl.id)
      JOIN forge.inventory_documents d ON (d.organization_id,d.id)=(pd.organization_id,pd.id)
      WHERE pl.organization_id=:org AND pl.order_id=:id AND d.status='POSTED' GROUP BY 
pl.order_line_id"""),
                {"org": ctx.organization_id, "id": id},
            )
        ).mappings()
    }


async def save(
    db: AsyncSession,
    ctx: RuntimeContext,
    body: PurchaseOrderInput,
    key: str,
    id: UUID | None = None,
    expected: int | None = None,
):
    require(ctx, "purchase.order.write")

    async def execute():
        before = await load(db, ctx, id, True) if id else None
        if before:
            version(before, expected, {"DRAFT"})
        previous = await raw_lines(db, ctx, id) if id else []
        if not body.reason.strip():
            raise Problem(422, "REASON_REQUIRED", "请填写采购原因")
        names = await refs(
            db,
            ctx,
            body.supplier_id,
            body.warehouse_id,
            [x.product_id for x in body.lines],
            [x.unit_id for x in body.lines],
        )
        captured = []
        for item in sorted(body.lines, key=lambda x: (x.product_id, x.unit_id)):
            pu = (
                (
                    await db.execute(
                        text(
                            (
                                "SELECT * FROM forge.product_units WHERE organization_id=:org AND "
                                "product_id=:p AND unit_id=:u AND active FOR SHARE"
                            )
                        ),
                        {"org": ctx.organization_id, "p": item.product_id, "u": item.unit_id},
                    )
                )
                .mappings()
                .first()
            )
            if pu is None:
                raise Problem(409, "MISSING_UNIT_CONVERSION", "商品单位换算不可用")
            snap = ConversionSnapshot.capture(
                item.product_id, item.unit_id, item.qty, pu["unit_to_base_factor"], pu["version"]
            )
            captured.append(
                snap.model_dump()
                | {
                    "id": uuid4(),
                    "unit_price": item.unit_price,
                    "amount": line_amount(item.qty, item.unit_price),
                    "product_label": names[("products", item.product_id)]["sku"]
                    + " · "
                    + names[("products", item.product_id)]["name"],
                    "unit_label": names[("units", item.unit_id)]["name"],
                }
            )
        with localcontext() as dec:
            dec.prec = 50
            amount = exact(sum((x["amount"] for x in captured), Decimal(0)), 4)
        params = {
            "org": ctx.organization_id,
            "id": id or uuid4(),
            "supplier": body.supplier_id,
            "supplier_name": names[("suppliers", body.supplier_id)]["name"],
            "wh": body.warehouse_id,
            "warehouse_name": names[("warehouses", body.warehouse_id)]["name"],
            "reason": body.reason,
            "amount": amount,
            "actor": ctx.user_id,
            "number": "PO-" + uuid4().hex[:16].upper(),
        }
        if id:
            await db.execute(
                text(
                    "DELETE FROM forge.purchase_order_lines WHERE organization_id=:org "
                    "AND order_id=:id"
                ),
                params,
            )
            sql = (
                "UPDATE forge.purchase_orders SET "
                "supplier_id=:supplier,supplier_name=:supplier_name,warehouse_id=:wh,warehouse_name=:warehouse_name,reason=:reason,amount=:amount,version=version+1"
                " WHERE organization_id=:org AND id=:id RETURNING *"
            )
        else:
            sql = (
                "INSERT INTO "
                "forge.purchase_orders(id,organization_id,number,supplier_id,supplier_name,warehouse_id,warehouse_name,reason,amount,created_by)"
                " "
                "VALUES(:id,:org,:number,:supplier,:supplier_name,:wh,:warehouse_name,:reason,:amount,:actor)"
                " RETURNING *"
            )
        row = dict((await db.execute(text(sql), params)).mappings().one())
        for n, item in enumerate(captured, 1):
            await db.execute(
                text(
                    "INSERT INTO "
                    "forge.purchase_order_lines(id,organization_id,order_id,line_no,product_id,unit_id,product_label,unit_label,qty,unit_to_base_factor,base_qty,conversion_version,unit_price,amount)"
                    " "
                    "VALUES(:id,:org,:order_id,:n,:product_id,:unit_id,:product_label,:unit_label,:qty,:unit_to_base_factor,:base_qty,:conversion_version,:unit_price,:amount)"
                ),
                item | {"org": ctx.organization_id, "order_id": row["id"], "n": n},
            )
        await record_mutation(
            db,
            ctx,
            "purchase.order.update" if id else "purchase.order.create",
            "purchase_order",
            row["id"],
            {"order": before, "lines": previous} if before else None,
            {"version": row["version"], "order": row, "lines": captured},
        )
        return receipt(ctx, row)

    return await inventory_once(
        db,
        ctx,
        f"purchase.order.save:{id or 'new'}",
        key,
        body.model_dump(mode="json") | {"expected_version": expected},
        execute,
    )


async def transition(db, ctx, id, expected, key, action, reason=None):
    require(ctx, "purchase.order." + action)

    async def execute():
        row = await load(db, ctx, id, True)
        version(
            row,
            expected,
            {"DRAFT"}
            if action == "confirm"
            else {"DRAFT", "CONFIRMED"}
            if action == "cancel"
            else {"CONFIRMED"},
        )
        if action == "confirm":
            lines = await raw_lines(db, ctx, id)
            await refs(
                db,
                ctx,
                row["supplier_id"],
                row["warehouse_id"],
                [x["product_id"] for x in lines],
                [x["unit_id"] for x in lines],
            )
            for line in sorted(lines, key=lambda x: (x["product_id"], x["unit_id"])):
                current = (
                    await db.execute(
                        text(
                            "SELECT version FROM forge.product_units WHERE organization_id=:org "
                            "AND product_id=:p AND unit_id=:u AND active FOR SHARE"
                        ),
                        {"org": ctx.organization_id, "p": line["product_id"], "u": line["unit_id"]},
                    )
                ).scalar()
                if current != line["conversion_version"]:
                    raise Problem(409, "UNIT_CONVERSION_CHANGED", "换算已变化，请编辑订单重新确认")
        else:
            if not reason or not reason.strip():
                raise Problem(422, "REASON_REQUIRED", "请填写操作原因")
            if action == "cancel" and any(
                x["received"] for x in (await quantities(db, ctx, id)).values()
            ):
                raise Problem(409, "ORDER_HAS_RECEIPTS", "已有有效收货，请关闭剩余采购")
        state, column = {
            "confirm": ("CONFIRMED", "confirmed_at"),
            "cancel": ("CANCELLED", "cancelled_at"),
            "close": ("CLOSED", "closed_at"),
        }[action]
        result = dict(
            (
                await db.execute(
                    text(
                        f"UPDATE forge.purchase_orders SET status=:state,{column}=now(),"
                        "version=version+1,action_reason=:reason "
                        "WHERE organization_id=:org AND id=:id RETURNING *"
                    ),
                    {"org": ctx.organization_id, "id": id, "state": state, "reason": reason},
                )
            )
            .mappings()
            .one()
        )
        await record_mutation(
            db, ctx, "purchase.order." + action, "purchase_order", id, row, result
        )
        return receipt(ctx, result)

    return await inventory_once(
        db,
        ctx,
        f"purchase.order.{action}:{id}",
        key,
        {"version": expected, "reason": reason},
        execute,
    )


async def inventory_once(db, ctx, operation, key, body, command):
    try:
        return await shared_once(db, ctx, operation, key, body, command)
    except InventoryError as exc:
        raise Problem(
            409 if exc.code in {"OVER_RETURN", "PURCHASE_PRICE_PRECISION_CONFLICT"} else 422,
            exc.code,
            exc.detail,
        ) from exc
