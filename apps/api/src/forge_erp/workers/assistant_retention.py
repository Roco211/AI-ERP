"""Private assistant body retention; independent from model availability."""

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from forge_erp.core.config import settings
from forge_erp.modules.assistant.application.retention import drain
from forge_erp.workers.celery_app import celery_app


async def run(organization_id: UUID | None = None, limit: int = 100):
    engine = create_async_engine(settings().database_url, poolclass=NullPool)
    try:
        return await drain(
            async_sessionmaker(engine, expire_on_commit=False), organization_id, limit
        )
    finally:
        await engine.dispose()


@celery_app.task(name="forge.assistant.retention")
def purge_assistant_bodies():
    return asyncio.run(run())


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Purge expired private assistant bodies")
    parser.add_argument("--organization", type=UUID)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("limit must be 1..100")
    print(json.dumps(asyncio.run(run(args.organization, args.limit))))
