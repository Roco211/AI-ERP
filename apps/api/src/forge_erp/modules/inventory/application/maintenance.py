"""Explicitly scoped operator maintenance using actual persisted permissions."""

from uuid import UUID

from sqlalchemy import create_engine, text

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions, set_tenant
from forge_erp.modules.inventory.application.engine import InventoryEngine


def operator_context(organization: str, email: str) -> RuntimeContext:
    # This trusted CLI requires migration credentials. They never enter the web/API context.
    with create_engine(settings().migration_database_url).connect() as db:
        row = db.execute(
            text(
                "SELECT o.id AS org,u.id AS actor FROM forge.organizations o "
                "JOIN forge.users u ON u.organization_id=o.id WHERE o.code=:code AND "
                "u.email=:email "
                "AND o.active AND u.active"
            ),
            {"code": organization, "email": email.lower()},
        ).one_or_none()
        if row is None:
            raise ValueError("Active organization/operator not found")
        permissions = frozenset(
            db.execute(
                text(
                    "SELECT DISTINCT rp.permission_code "
                    "FROM forge.user_roles ur JOIN forge.role_permissions rp "
                    "ON (rp.organization_id,rp.role_id)=(ur.organization_id,ur.role_id) "
                    "WHERE ur.organization_id=:org AND ur.user_id=:actor"
                ),
                {"org": row.org, "actor": row.actor},
            ).scalars()
        )
    return RuntimeContext(row.org, row.actor, permissions, "inventory-maintenance", source="SYSTEM")


async def reconcile_scope(
    ctx: RuntimeContext, warehouse: UUID, products: list[UUID], repair: bool = False
) -> list[dict]:
    ctx.require("inventory.reconcile")
    ctx.require("product.cost.read")
    if not products or len(products) > 200:
        raise ValueError("Specify 1–200 product IDs")
    async with sessions.begin() as db:
        await set_tenant(db, ctx.organization_id)
        e = InventoryEngine(db, ctx, "inventory.reconcile")
        await e.lock([(warehouse, p) for p in products], historical=True)
        report = await e.reconcile(repair=repair)
        if not repair:
            # Any zero-row initialization is rolled back; dry run leaves no persistent writes.
            await db.rollback()
        return report
