"""Independent persistent import polling; embeddings and Outbox backlog cannot block it."""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from forge_erp.core.config import settings
from forge_erp.modules.catalog_import.application.runner import drain
from forge_erp.workers.celery_app import celery_app


async def run(organization_id=None, limit=100):
    engine = create_async_engine(settings().database_url, poolclass=NullPool)
    try:
        return await drain(
            async_sessionmaker(engine, expire_on_commit=False), organization_id, limit
        )
    finally:
        await engine.dispose()


@celery_app.task(name="forge.catalog_import.poll")
def poll_imports():
    return asyncio.run(run())


if __name__ == "__main__":
    import argparse
    import json
    from uuid import UUID

    parser = argparse.ArgumentParser(
        description="Resume confirmed import rows and purge expired bodies"
    )
    parser.add_argument("--organization", type=UUID)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000:
        parser.error("limit must be 1..10000")
    print(json.dumps(asyncio.run(run(args.organization, args.limit))))
