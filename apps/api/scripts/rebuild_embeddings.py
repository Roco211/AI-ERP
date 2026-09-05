"""Rebuild only the requested organization's derived product vectors."""

import argparse
import asyncio
from uuid import UUID

from sqlalchemy import create_engine, text

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import engine, sessions, set_tenant
from forge_erp.modules.catalog.application.semantic import rebuild


async def run(organization: str) -> None:
    cfg = settings()
    if not cfg.embedding_enabled:
        raise RuntimeError("Enable the pinned local embedding model first")
    # Administrative CLI resolves the tenant; normal APIs never accept organization IDs.
    with create_engine(cfg.migration_database_url).connect() as db:
        org = db.execute(
            text("SELECT id FROM forge.organizations WHERE code=:code"), {"code": organization}
        ).scalar_one()
        actor = db.execute(
            text(
                "SELECT id FROM forge.users WHERE organization_id=:org "
                "AND active ORDER BY created_at LIMIT 1"
            ),
            {"org": org},
        ).scalar_one()
    ctx = RuntimeContext(
        UUID(str(org)),
        UUID(str(actor)),
        frozenset({"catalog.read", "catalog.write"}),
        "embedding-rebuild",
        "SYSTEM",
    )
    total = 0
    while True:
        async with sessions.begin() as db:
            await set_tenant(db, ctx.organization_id)
            count = await rebuild(db, ctx, limit=20)
        total += count
        if not count:
            break
    await engine.dispose()
    print(f"Indexed {total} products for organization {organization}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization", required=True)
    asyncio.run(run(parser.parse_args().organization))
