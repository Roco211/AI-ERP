"""Opt-in demo catalog, idempotent commands using forge_app and tenant context."""

import asyncio

from sqlalchemy import text

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import engine, sessions, set_tenant
from forge_erp.modules.catalog.application.service import write_command
from forge_erp.modules.catalog.infrastructure.resources import RESOURCES


async def seed_catalog() -> None:
    if settings().app_env != "development":
        raise RuntimeError("Catalog demo seed is development-only")
    # Resolve only the existing DEMO identity using the bootstrap administrator connection.
    from sqlalchemy import create_engine

    with create_engine(settings().migration_database_url).connect() as lookup:
        identity = lookup.execute(
            text(
                "SELECT o.id,u.id FROM forge.organizations o JOIN forge.users u "
                "ON u.organization_id=o.id WHERE o.code='DEMO' ORDER BY u.created_at LIMIT 1"
            )
        ).one()
        permissions = frozenset(
            lookup.execute(text("SELECT code FROM forge.permissions")).scalars()
        )
    ctx = RuntimeContext(identity[0], identity[1], permissions, "catalog-dev-seed", source="SYSTEM")
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)

        async def add(resource: str, body: dict) -> dict:
            code_field = "sku" if resource == "products" else "code"
            if code_field in body:
                row = (
                    (
                        await db.execute(
                            text(
                                f"SELECT * FROM forge.{RESOURCES[resource].table} "
                                f"WHERE organization_id=:org AND {code_field}=:code"
                            ),
                            {"org": ctx.organization_id, "code": body[code_field]},
                        )
                    )
                    .mappings()
                    .first()
                )
                if row:
                    return dict(row)
            import hashlib
            import json

            key = (
                "demo-v05:"
                + hashlib.sha256(json.dumps(body, default=str, sort_keys=True).encode()).hexdigest()
            )
            return await write_command(db, ctx, resource, body, key)

        pcs = await add("units", {"code": "PCS", "name": "个"})
        box = await add("units", {"code": "BOX", "name": "箱"})
        brand = await add("brands", {"code": "FORGE", "name": "Forge 示例品牌"})
        category = await add(
            "categories",
            {
                "code": "FASTENERS",
                "name": "紧固件",
                "attribute_schema": [
                    {
                        "key": "材质",
                        "label": "材质",
                        "kind": "enum",
                        "required": True,
                        "options": ["304", "316"],
                    },
                    {
                        "key": "长度",
                        "label": "长度",
                        "kind": "decimal",
                        "unit": "mm",
                        "required": True,
                    },
                ],
            },
        )
        customer = await add(
            "customers",
            {
                "code": "DEMO-C01",
                "name": "兴达五金门店",
                "price_tier": "wholesale",
                "contact": "示例客户",
            },
        )
        supplier = await add(
            "suppliers", {"code": "DEMO-S01", "name": "恒丰紧固件供应商", "contact": "示例供应商"}
        )
        await add("warehouses", {"code": "MAIN", "name": "主仓库", "address": "示例仓库地址"})
        for i, (diameter, length) in enumerate(
            [(6, 20), (6, 30), (8, 20), (8, 30), (8, 40), (10, 30), (10, 50), (12, 40)]
        ):
            product = await add(
                "products",
                {
                    "sku": f"BOLT-304-M{diameter}-{length}",
                    "barcode": f"69000000000{i:02}",
                    "name": f"304不锈钢外六角螺栓 M{diameter}×{length}",
                    "category_id": category["id"],
                    "brand_id": brand["id"],
                    "base_unit_id": pcs["id"],
                    "specification": f"M{diameter}×{length}",
                    "attributes": {"材质": "304", "长度": str(length)},
                    "min_stock_qty": "100",
                    "reorder_qty": "1000",
                    "preferred_supplier_id": supplier["id"],
                },
            )
            await add(
                "product-units",
                {"product_id": product["id"], "unit_id": box["id"], "unit_to_base_factor": "1000"},
            )
            for tier, price in [("standard", "0.80"), ("wholesale", "0.65"), ("retail", "1.00")]:
                await add(
                    "product-prices",
                    {"product_id": product["id"], "price_type": tier, "price": price},
                )
            await add(
                "supplier-products",
                {
                    "product_id": product["id"],
                    "supplier_id": supplier["id"],
                    "supplier_sku": f"HF-{diameter}-{length}",
                    "purchase_unit_id": box["id"],
                    "lead_days": 3,
                },
            )
        await add(
            "product-prices",
            {
                "product_id": product["id"],
                "price_type": "customer",
                "customer_id": customer["id"],
                "price": "0.60",
            },
        )
        tools = await add("categories", {"code": "TOOLS", "name": "工具与辅料"})
        for code, name, specification in [
            ("WRENCH-250", "活动扳手 250mm", "可调节开口，拧紧或松开不同尺寸的六角螺母"),
            ("DRIVER-PH2", "十字螺丝刀 PH2", "拆装十字槽螺钉"),
            ("PTFE-TAPE", "聚四氟乙烯生料带", "水管螺纹接口密封防漏"),
            ("INSULATION-TAPE", "电工绝缘胶带", "包扎电线接头，绝缘防漏电"),
        ]:
            await add(
                "products",
                {
                    "sku": code,
                    "name": name,
                    "category_id": tools["id"],
                    "base_unit_id": pcs["id"],
                    "specification": specification,
                },
            )
    await engine.dispose()
    print("Demo catalog seeded through tenant-scoped application commands.")


if __name__ == "__main__":
    asyncio.run(seed_catalog())
