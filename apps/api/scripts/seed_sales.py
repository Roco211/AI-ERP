"""Optional sales sample for the existing DEMO operator; never resets existing data."""

import asyncio
import json
import os
from pathlib import Path

from dotenv import dotenv_values

from forge_erp.core.config import settings
from forge_erp.core.db import engine
from forge_erp.modules.inventory.application.maintenance import operator_context
from forge_erp.modules.sales.application.dev_seed import seed_sales

ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


async def main():
    if settings().app_env != "development":
        raise RuntimeError("Sales demonstration seed is development-only")
    values = {**dotenv_values(ROOT_ENV), **os.environ}
    email = values.get("SEED_ADMIN_EMAIL")
    if not email:
        raise RuntimeError("SEED_ADMIN_EMAIL must name an existing active DEMO operator")
    ctx = operator_context("DEMO", str(email))
    try:
        result = await seed_sales(ctx)
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
