"""Optional inventory demo, isolated from existing product/warehouse balances."""

import asyncio
import os
from pathlib import Path
from uuid import UUID

from dotenv import dotenv_values
from sqlalchemy import text

from forge_erp.core.config import settings
from forge_erp.core.db import engine, sessions, set_tenant
from forge_erp.modules.catalog.application.service import write_command
from forge_erp.modules.inventory.application.documents import save_draft, transition
from forge_erp.modules.inventory.application.maintenance import operator_context
from forge_erp.modules.inventory.domain.schemas import OpeningInput

ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


async def seed_inventory():
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
                db, ctx, resource, {field: code, "name": name} | extra, "inventory-demo-" + resource
            )

        category = await add("categories", "categories", "INV-DEMO", "库存演示分类")
        unit = await add("units", "units", "INV-PCS", "个（演示）")
        wh = await add("warehouses", "warehouses", "INV-DEMO", "库存演示仓")
        product = await add(
            "products",
            "products",
            "INV-DEMO-BOLT",
            "库存演示螺栓",
            category_id=str(category["id"]),
            base_unit_id=str(unit["id"]),
            min_stock_qty="20",
        )
        exists = (
            await db.execute(
                text(
                    "SELECT 1 FROM forge.inventory_document_lines l "
                    "JOIN forge.inventory_documents d ON (d.organization_id,"
                    "d.id)=(l.organization_id,l.document_id) "
                    "WHERE l.organization_id=:org AND l.product_id=:product AND "
                    "d.warehouse_id=:wh LIMIT 1"
                ),
                {"org": ctx.organization_id, "product": product["id"], "wh": wh["id"]},
            )
        ).first()
        if exists:
            print("Existing inventory demo documents preserved.")
            return
        body = OpeningInput(
            warehouse_id=wh["id"],
            reason="库存演示期初（可选开发样例）",
            lines=[
                {
                    "product_id": product["id"],
                    "unit_id": unit["id"],
                    "qty": "100",
                    "input_unit_cost": "1.25",
                }
            ],
        )
        doc = await save_draft(db, ctx, "OPENING", body, "inventory-demo-opening")
        await transition(db, ctx, UUID(doc["id"]), doc["version"], "inventory-demo-post", "post")
    print("Inventory demo: 100 units in the dedicated demo warehouse; existing balances untouched.")


if __name__ == "__main__":

    async def main():
        try:
            await seed_inventory()
        finally:
            await engine.dispose()

    asyncio.run(main())
