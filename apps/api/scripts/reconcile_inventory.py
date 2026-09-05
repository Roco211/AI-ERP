"""Scoped inventory reconciliation. Dry-run unless --repair is explicitly selected."""

import argparse
import asyncio
import json
from uuid import UUID

from forge_erp.core.db import engine, verify_database_role
from forge_erp.modules.inventory.application.maintenance import operator_context, reconcile_scope


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", required=True)
    parser.add_argument("--actor", required=True, help="Existing authorized operator email")
    parser.add_argument("--warehouse", required=True, type=UUID)
    parser.add_argument("--product", required=True, action="append", type=UUID)
    parser.add_argument("--repair", action="store_true")
    args = parser.parse_args()
    try:
        await verify_database_role()
        ctx = operator_context(args.organization, args.actor)
        print(
            json.dumps(
                await reconcile_scope(ctx, args.warehouse, args.product, args.repair),
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
