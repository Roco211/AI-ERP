import json
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
from forge_erp.modules.sales.application import pricing, reservations
from forge_erp.modules.sales.domain.schemas import SalesOrderInput
from forge_erp.modules.sales.domain.values import line_amount


async def load(db: AsyncSession, ctx: RuntimeContext, id: UUID, lock=False) -> dict:
    row = (
        (
            await db.execute(
                text(
                    "SELECT * FROM forge.sales_orders WHERE organization_id=:org AND id=:id"
                    + (" FOR UPDATE" if lock else "")
                ),
                {"org": ctx.organization_id, "id": id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "NOT_FOUND", "销售订单不存在或无权访问")
    return dict(row)


def require(ctx: RuntimeContext, permission: str):
    ctx.require(permission)
    if permission in {"sales.order.write", "sales.order.confirm"}:
        ctx.require("product.price.read")


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


async def refs(db, ctx, customer, warehouse, products, units):
    names = {}
    for table, ids in [
        ("customers", [customer]),
        ("warehouses", [warehouse] if warehouse else []),
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
                    409, "INVALID_REFERENCE", "客户、仓库、商品或单位不存在、已停用或无权使用"
                )
            names[(table, id)] = dict(row)
    return names


async def raw_lines(db, ctx, id):
    return [
        dict(x)
        for x in (
            await db.execute(
                text(
                    "SELECT * FROM forge.sales_order_lines WHERE organization_id=:org "
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
      coalesce(sum(il.base_qty) FILTER(WHERE pd.kind='SHIPMENT'),0) AS shipped,
      coalesce(sum(il.base_qty) FILTER(WHERE pd.kind='RETURN'),0) AS returned
      FROM forge.sales_document_lines pl JOIN forge.sales_documents pd
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
    body: SalesOrderInput,
    key: str,
    id: UUID | None = None,
    expected: int | None = None,
):
    require(ctx, "sales.order.write")

    async def execute():
        before = await load(db, ctx, id, True) if id else None
        if before:
            version(before, expected, {"DRAFT"})
        previous = await raw_lines(db, ctx, id) if id else []
        if not body.reason.strip():
            raise Problem(422, "REASON_REQUIRED", "请填写销售原因")
        names = await refs(
            db,
            ctx,
            body.customer_id,
            body.warehouse_id,
            [x.product_id for x in body.lines],
            [x.unit_id for x in body.lines],
        )
        captured = []
        for item in sorted(body.lines, key=lambda x: (x.product_id, x.unit_id)):
            pu = await conversion(db, ctx, item.product_id, item.unit_id)
            if item.pricing_mode == "AUTO":
                quote = await pricing.resolve(
                    db,
                    ctx,
                    names[("customers", body.customer_id)],
                    item.product_id,
                    item.unit_id,
                    pu["unit_to_base_factor"],
                    names[("products", item.product_id)]["base_unit_id"],
                )
                price = quote["unit_price"]
                source = quote["price_source"]
                if price is None:
                    raise Problem(409, "PRICE_UNSET", "商品未设置销售价，请明确输入单价")
            else:
                price = item.unit_price
                source = {
                    "source": "manual",
                    "target_unit_id": str(item.unit_id),
                    "target_factor": str(pu["unit_to_base_factor"]),
                }
            if price is None:
                raise Problem(422, "PRICE_REQUIRED", "请明确填写销售单价")
            snap = ConversionSnapshot.capture(
                item.product_id, item.unit_id, item.qty, pu["unit_to_base_factor"], pu["version"]
            )
            captured.append(
                snap.model_dump()
                | {
                    "id": uuid4(),
                    "unit_price": price,
                    "pricing_mode": item.pricing_mode,
                    "price_source": source,
                    "amount": line_amount(item.qty, price),
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
            "customer": body.customer_id,
            "customer_name": names[("customers", body.customer_id)]["name"],
            "wh": body.warehouse_id,
            "warehouse_name": names[("warehouses", body.warehouse_id)]["name"],
            "reason": body.reason,
            "amount": amount,
            "actor": ctx.user_id,
            "number": "SO-" + uuid4().hex[:16].upper(),
        }
        if id:
            await db.execute(
                text(
                    "DELETE FROM forge.sales_order_lines WHERE organization_id=:org "
                    "AND order_id=:id"
                ),
                params,
            )
            sql = (
                "UPDATE forge.sales_orders SET "
                "customer_id=:customer,customer_name=:customer_name,warehouse_id=:wh,warehouse_name=:warehouse_name,reason=:reason,amount=:amount,version=version+1"
                " WHERE organization_id=:org AND id=:id RETURNING *"
            )
        else:
            sql = (
                "INSERT INTO "
                "forge.sales_orders(id,organization_id,number,customer_id,customer_name,warehouse_id,warehouse_name,reason,amount,created_by)"
                " "
                "VALUES(:id,:org,:number,:customer,:customer_name,:wh,:warehouse_name,:reason,:amount,:actor)"
                " RETURNING *"
            )
        row = dict((await db.execute(text(sql), params)).mappings().one())
        for n, item in enumerate(captured, 1):
            await db.execute(
                text(
                    "INSERT INTO "
                    "forge.sales_order_lines(id,organization_id,order_id,line_no,product_id,unit_id,product_label,unit_label,qty,unit_to_base_factor,base_qty,conversion_version,unit_price,amount,pricing_mode,price_source)"
                    " "
                    "VALUES(:id,:org,:order_id,:n,:product_id,:unit_id,:product_label,:unit_label,"
                    ":qty,:unit_to_base_factor,:base_qty,:conversion_version,:unit_price,:amount,"
                    ":pricing_mode,CAST(:price_source AS jsonb))"
                ),
                item
                | {
                    "org": ctx.organization_id,
                    "order_id": row["id"],
                    "n": n,
                    "price_source": json.dumps(item["price_source"]),
                },
            )
        await record_mutation(
            db,
            ctx,
            "sales.order.update" if id else "sales.order.create",
            "sales_order",
            row["id"],
            {"order": before, "lines": previous} if before else None,
            {"version": row["version"], "order": row, "lines": captured},
        )
        return receipt(ctx, row)

    return await inventory_once(
        db,
        ctx,
        f"sales.order.save:{id or 'new'}",
        key,
        body.model_dump(mode="json") | {"expected_version": expected},
        execute,
    )


async def transition(db, ctx, id, expected, key, action, reason=None):
    if action not in {"confirm", "cancel", "close"}:
        raise Problem(422, "INVALID_ACTION", "无效销售操作")
    require(ctx, "sales.order." + action)

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
        lines = await raw_lines(db, ctx, id)
        if action == "confirm":
            await refs(
                db,
                ctx,
                row["customer_id"],
                row["warehouse_id"],
                [x["product_id"] for x in lines],
                [x["unit_id"] for x in lines],
            )
            for line in sorted(lines, key=lambda x: (x["product_id"], x["unit_id"])):
                current = (await conversion(db, ctx, line["product_id"], line["unit_id"]))[
                    "version"
                ]
                if current != line["conversion_version"]:
                    raise Problem(409, "UNIT_CONVERSION_CHANGED", "换算已变化，请编辑订单重新确认")
        else:
            if not reason or not reason.strip():
                raise Problem(422, "REASON_REQUIRED", "请填写操作原因")
            if action == "cancel" and any(
                x["shipped"] for x in (await quantities(db, ctx, id)).values()
            ):
                raise Problem(409, "ORDER_HAS_SHIPMENTS", "已有有效出库，请关闭未发部分")
        if action == "confirm":
            await reservations.reserve(db, ctx, row, lines)
        elif row["status"] == "CONFIRMED":
            await reservations.release(db, ctx, row, action)
        state, column = {
            "confirm": ("CONFIRMED", "confirmed_at"),
            "cancel": ("CANCELLED", "cancelled_at"),
            "close": ("CLOSED", "closed_at"),
        }[action]
        result = dict(
            (
                await db.execute(
                    text(
                        f"UPDATE forge.sales_orders SET status=:state,{column}=now(),"
                        "version=version+1,action_reason=:reason "
                        "WHERE organization_id=:org AND id=:id RETURNING *"
                    ),
                    {"org": ctx.organization_id, "id": id, "state": state, "reason": reason},
                )
            )
            .mappings()
            .one()
        )
        await record_mutation(db, ctx, "sales.order." + action, "sales_order", id, row, result)
        return receipt(ctx, result)

    return await inventory_once(
        db,
        ctx,
        f"sales.order.{action}:{id}",
        key,
        {"version": expected, "reason": reason},
        execute,
    )


async def inventory_once(db, ctx, operation, key, body, command):
    try:
        return await shared_once(db, ctx, operation, key, body, command)
    except InventoryError as exc:
        raise Problem(
            409 if exc.code == "PRICE_PRECISION_CONFLICT" else 422,
            exc.code,
            exc.detail,
        ) from exc


async def conversion(db, ctx, product, unit):
    row = (
        (
            await db.execute(
                text(
                    "SELECT * FROM forge.product_units "
                    "WHERE organization_id=:org AND product_id=:p "
                    "AND unit_id=:u AND active FOR SHARE"
                ),
                {"org": ctx.organization_id, "p": product, "u": unit},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(409, "MISSING_UNIT_CONVERSION", "商品单位换算不可用")
    return dict(row)
