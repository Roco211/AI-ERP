"""Opt-in sales demonstration; existing business facts are never repaired or replaced."""

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import text

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions, set_tenant, verify_database_role
from forge_erp.modules.catalog.application.service import write_command
from forge_erp.modules.inventory.application import documents as inventory
from forge_erp.modules.inventory.domain.schemas import OpeningInput
from forge_erp.modules.sales.application import documents, orders, queries, returns
from forge_erp.modules.sales.domain.schemas import (
    SalesOrderInput,
    SalesReturnInput,
    SalesShipmentInput,
)

SEED_MARKER = "forge-sales-demo-v08"
REQUIRED_PERMISSIONS = frozenset(
    {
        "catalog.write",
        "customer.write",
        "warehouse.write",
        "product.price.write",
        "product.price.read",
        "product.cost.read",
        "inventory.opening",
        "inventory.read",
        "sales.read",
        "sales.order.write",
        "sales.order.confirm",
        "sales.ship",
        "sales.return",
    }
)


async def verified_original_order(db, ctx, audit):
    """Audit locates the original sample; immutable source facts verify that association."""
    try:
        original = {}
        for resource, field, code, _ in DEDICATED_RECORDS:
            matches = [
                item
                for item in audit
                if item["action"] == f"catalog.{resource}.create"
                and item["resource_type"] == resource
                and isinstance(item["after"], dict)
                and item["after"].get(field) == code
                and item["after"].get("id") == str(item["resource_id"])
            ]
            if len(matches) != 1:
                return None
            original[resource] = matches[0]["resource_id"]
        created = [
            item
            for item in audit
            if item["action"] == "sales.order.create" and item["resource_type"] == "sales_order"
        ]
        if len(created) != 1:
            return None
        order_id = created[0]["resource_id"]
        snapshot = created[0]["after"]
        header, line = snapshot["order"], snapshot["lines"][0]
        if (
            len(snapshot["lines"]) != 1
            or header["id"] != str(order_id)
            or header["organization_id"] != str(ctx.organization_id)
            or header["customer_id"] != str(original["customers"])
            or header["warehouse_id"] != str(original["warehouses"])
            or line["product_id"] != str(original["products"])
            or line["unit_id"] != str(original["units"])
            or Decimal(line["qty"]) != 100
            or Decimal(line["unit_price"]) != 15
        ):
            return None
        documents_by_kind = {}
        for item in audit:
            if item["action"] != "sales.document.post" or item["resource_type"] != "sales_document":
                continue
            document = item["after"]["document"]
            kind = document["kind"]
            if (
                kind not in {"SHIPMENT", "RETURN"}
                or kind in documents_by_kind
                or document["type"] != "SALES_" + kind
                or document["id"] != str(item["resource_id"])
                or document["order_id"] != str(order_id)
                or document["organization_id"] != str(ctx.organization_id)
                or document["status"] != "POSTED"
            ):
                return None
            documents_by_kind[kind] = item["resource_id"]
        if set(documents_by_kind) != {"SHIPMENT", "RETURN"}:
            return None
    except KeyError, IndexError, TypeError, ValueError, InvalidOperation:
        return None
    # Current statuses and master labels may have changed; these original facts cannot change.
    return (
        await db.execute(
            text(
                "SELECT o.id FROM forge.sales_orders o "
                "JOIN forge.sales_order_lines ol "
                "ON (ol.organization_id,ol.order_id)=(o.organization_id,o.id) "
                "JOIN forge.sales_documents ss "
                "ON (ss.organization_id,ss.order_id)=(o.organization_id,o.id) "
                "JOIN forge.sales_documents sr "
                "ON (sr.organization_id,sr.order_id,sr.original_document_id)="
                "(o.organization_id,o.id,ss.id) "
                "JOIN forge.inventory_movements sm "
                "ON (sm.organization_id,sm.document_id,sm.operation_id)="
                "(ss.organization_id,ss.id,ss.id) AND sm.kind='ISSUE' "
                "JOIN forge.inventory_movements rm "
                "ON (rm.organization_id,rm.document_id,rm.operation_id)="
                "(sr.organization_id,sr.id,sr.id) AND rm.kind='RECEIVE' "
                "WHERE o.organization_id=:org AND o.id=:order AND o.customer_id=:customer "
                "AND o.warehouse_id=:warehouse AND ol.product_id=:product AND ol.unit_id=:unit "
                "AND ol.qty=100 AND ol.base_qty=100 AND ol.unit_price=15 "
                "AND ss.id=:shipment AND ss.kind='SHIPMENT' "
                "AND sr.id=:returned AND sr.kind='RETURN' "
                "AND sm.product_id=:product AND rm.product_id=:product "
                "AND sm.warehouse_id=:warehouse AND rm.warehouse_id=:warehouse "
                "AND sm.base_qty=-60 AND sm.value_delta=-660 "
                "AND rm.base_qty=10 AND rm.value_delta=110"
            ),
            {
                "org": ctx.organization_id,
                "order": order_id,
                "customer": original["customers"],
                "warehouse": original["warehouses"],
                "product": original["products"],
                "unit": original["units"],
                "shipment": documents_by_kind["SHIPMENT"],
                "returned": documents_by_kind["RETURN"],
            },
        )
    ).scalar_one_or_none()


DEDICATED_RECORDS = (
    ("categories", "code", "SALE-DEMO", "销售演示分类"),
    ("units", "code", "SALE-DEMO-PCS", "个（销售演示）"),
    ("warehouses", "code", "SALE-DEMO", "销售演示仓"),
    ("customers", "code", "SALE-DEMO", "销售演示客户"),
    ("products", "sku", "SALE-DEMO-BOLT", "销售演示螺栓"),
)


async def existing_report(db, ctx, status, order_id):
    result = {
        "status": status,
        "message": (
            "销售演示已创建，可从订单继续出库或查看原单退货。"
            if status == "created"
            else "已保留原销售演示的当前资料和业务事实；本次未写入、补建或重置，请以页面现状为准。"
        ),
        "order_id": str(order_id),
        "order": await queries.order_detail(db, ctx, order_id),
        "documents": (await queries.document_list(db, ctx, 1, 25, order=order_id))["items"],
    }
    return result


async def seed_sales(ctx: RuntimeContext) -> dict:
    """Own the complete transaction; CLI callers provide an active persisted operator context."""
    if settings().app_env != "development":
        raise RuntimeError("Sales demonstration seed is development-only")
    for permission in sorted(REQUIRED_PERMISSIONS):
        ctx.require(permission)
    await verify_database_role()
    # Audit is permanent (forge_app cannot update/delete it), unlike expiring idempotency rows.
    # Every command shares this marker and transaction; a failed run leaves no partial marker.
    ctx = replace(ctx, request_id=SEED_MARKER, source="SYSTEM")
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
            {"scope": SEED_MARKER + ":" + str(ctx.organization_id)},
        )
        audit = (
            (
                await db.execute(
                    text(
                        "SELECT action,resource_id,resource_type,after FROM forge.audit_events "
                        "WHERE organization_id=:org AND request_id=:marker AND source='SYSTEM'"
                    ),
                    {"org": ctx.organization_id, "marker": SEED_MARKER},
                )
            )
            .mappings()
            .all()
        )
        if audit:
            original_order = await verified_original_order(db, ctx, audit)
            if original_order is None:
                return {
                    "status": "preserved",
                    "message": (
                        "演示关联标记已有记录但无法完整核验原单来源；已保留全部数据，未自动补建。"
                    ),
                }
            return await existing_report(db, ctx, "preserved", original_order)
        collisions = []
        for table, field, code, _ in DEDICATED_RECORDS:
            exists = (
                await db.execute(
                    text(
                        f"SELECT id FROM forge.{table} WHERE organization_id=:org AND {field}=:code"
                    ),
                    {"org": ctx.organization_id, "code": code},
                )
            ).first()
            if exists:
                collisions.append(table + ":" + code)
        if collisions:
            return {
                "status": "preserved",
                "message": "演示专用编码已被现有资料占用；已保留原数据，整次未写入或接管这些资料。",
                "existing_codes": collisions,
            }

        async def add(resource, body):
            return await write_command(db, ctx, resource, body, SEED_MARKER + ":" + resource)

        category = await add("categories", {"code": "SALE-DEMO", "name": "销售演示分类"})
        unit = await add("units", {"code": "SALE-DEMO-PCS", "name": "个（销售演示）"})
        warehouse = await add("warehouses", {"code": "SALE-DEMO", "name": "销售演示仓"})
        customer = await add("customers", {"code": "SALE-DEMO", "name": "销售演示客户"})
        product = await add(
            "products",
            {
                "sku": "SALE-DEMO-BOLT",
                "name": "销售演示螺栓",
                "category_id": category["id"],
                "base_unit_id": unit["id"],
                "specification": "独立销售演示商品，用于练习开单、出库与原单退货",
            },
        )
        await add(
            "product-prices",
            {"product_id": product["id"], "price_type": "standard", "price": "15"},
        )
        opening = await inventory.save_draft(
            db,
            ctx,
            "OPENING",
            OpeningInput.model_validate(
                {
                    "warehouse_id": warehouse["id"],
                    "reason": "销售演示期初：200 个，基本单位成本 11",
                    "lines": [
                        {
                            "product_id": product["id"],
                            "unit_id": unit["id"],
                            "qty": "200",
                            "input_unit_cost": "11",
                        }
                    ],
                }
            ),
            SEED_MARKER + ":opening",
        )
        await inventory.transition(
            db, ctx, UUID(opening["id"]), opening["version"], SEED_MARKER + ":opening-post", "post"
        )
        order = await orders.save(
            db,
            ctx,
            SalesOrderInput.model_validate(
                {
                    "customer_id": customer["id"],
                    "warehouse_id": warehouse["id"],
                    "reason": "销售演示订单（初始样例：订购 100、出库 60、退货 10）",
                    "lines": [
                        {
                            "product_id": product["id"],
                            "unit_id": unit["id"],
                            "qty": "100",
                            "pricing_mode": "AUTO",
                        }
                    ],
                }
            ),
            SEED_MARKER + ":order",
        )
        order_id = UUID(order["id"])
        await orders.transition(
            db, ctx, order_id, order["version"], SEED_MARKER + ":confirm", "confirm"
        )
        line = (await orders.raw_lines(db, ctx, order_id))[0]
        shipment = await documents.save(
            db,
            ctx,
            SalesShipmentInput.model_validate(
                {
                    "source_id": order_id,
                    "reason": "销售演示分批出库：60 个",
                    "lines": [{"source_line_id": line["id"], "qty": "60"}],
                }
            ),
            SEED_MARKER + ":shipment",
        )
        shipment_id = UUID(shipment["id"])
        await documents.post(
            db, ctx, shipment_id, shipment["version"], SEED_MARKER + ":shipment-post"
        )
        shipped_line = (await queries.raw_document_lines(db, ctx, shipment_id))[0]
        returned = await returns.save(
            db,
            ctx,
            SalesReturnInput.model_validate(
                {
                    "source_id": shipment_id,
                    "reason": "销售演示原单退货：10 个",
                    "lines": [{"source_line_id": shipped_line["id"], "qty": "10"}],
                }
            ),
            SEED_MARKER + ":return",
        )
        await returns.post(
            db, ctx, UUID(returned["id"]), returned["version"], SEED_MARKER + ":return-post"
        )
        return await existing_report(db, ctx, "created", order_id)
