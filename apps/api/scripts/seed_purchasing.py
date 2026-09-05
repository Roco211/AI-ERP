"""Optional purchasing demo, isolated from existing product/warehouse balances."""

import asyncio
import os
from pathlib import Path
from uuid import UUID

from dotenv import dotenv_values
from sqlalchemy import text

from forge_erp.core.config import settings
from forge_erp.core.db import engine, sessions, set_tenant
from forge_erp.modules.catalog.application.service import write_command
from forge_erp.modules.inventory.application.maintenance import operator_context
from forge_erp.modules.purchasing.application import documents, orders
from forge_erp.modules.purchasing.domain.schemas import PurchaseDocumentInput, PurchaseOrderInput

ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


async def seed_purchasing():
    if settings().app_env != "development":
        raise RuntimeError("Development seed only")
    values = {**dotenv_values(ROOT_ENV), **os.environ}
    ctx = operator_context("DEMO", str(values["SEED_ADMIN_EMAIL"]))
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)

        async def add(resource, table, code, name, **extra):
            field = "sku" if resource == "products" else "code"
            row = (
                (
                    await db.execute(
                        text(
                            f"SELECT * FROM forge.{table} "
                            f"WHERE organization_id=:org AND {field}=:code"
                        ),
                        {"org": ctx.organization_id, "code": code},
                    )
                )
                .mappings()
                .first()
            )
            if row:
                return dict(row)
            return await write_command(
                db,
                ctx,
                resource,
                {field: code, "name": name} | extra,
                "purchasing-demo-" + resource,
            )

        category = await add("categories", "categories", "PUR-DEMO", "采购演示分类")
        unit = await add("units", "units", "PUR-PCS", "个（演示）")
        wh = await add("warehouses", "warehouses", "PUR-DEMO", "采购演示仓")
        product = await add(
            "products",
            "products",
            "PUR-DEMO-BOLT",
            "采购演示螺栓",
            category_id=str(category["id"]),
            base_unit_id=str(unit["id"]),
            min_stock_qty="20",
        )
        supplier = await add("suppliers", "suppliers", "PUR-DEMO", "采购演示供应商")
        exists = (
            await db.execute(
                text(
                    "SELECT 1 FROM forge.purchase_orders "
                    "WHERE organization_id=:org AND warehouse_id=:wh LIMIT 1"
                ),
                {"org": ctx.organization_id, "wh": wh["id"]},
            )
        ).first()
        if exists:
            print("Existing purchasing demo documents preserved.")
            return
        body = PurchaseOrderInput(
            supplier_id=supplier["id"],
            warehouse_id=wh["id"],
            reason="采购演示订单（可选开发样例）",
            lines=[
                {
                    "product_id": product["id"],
                    "unit_id": unit["id"],
                    "qty": "100",
                    "unit_price": "1.25",
                }
            ],
        )
        order = await orders.save(db, ctx, body, "purchasing-demo-order")
        await orders.transition(
            db, ctx, UUID(order["id"]), order["version"], "purchasing-demo-confirm", "confirm"
        )
        line = (await orders.raw_lines(db, ctx, UUID(order["id"])))[0]
        receipt = await documents.save(
            db,
            ctx,
            "RECEIPT",
            PurchaseDocumentInput(
                source_id=UUID(order["id"]),
                reason="采购演示第一批收货",
                lines=[{"source_line_id": line["id"], "qty": "60"}],
            ),
            "purchasing-demo-receipt",
        )
        await documents.transition(
            db, ctx, UUID(receipt["id"]), receipt["version"], "purchasing-demo-post", "post"
        )
    print("Purchasing demo: Order 100, received 60, remaining 40 in the demo warehouse.")


if __name__ == "__main__":

    async def main():
        try:
            await seed_purchasing()
        finally:
            await engine.dispose()

    asyncio.run(main())
