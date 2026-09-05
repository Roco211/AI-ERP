from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from forge_erp.core.config import settings

engine = create_async_engine(settings().database_url, pool_pre_ping=True, pool_size=5)
sessions = async_sessionmaker(engine, expire_on_commit=False)


async def set_tenant(db: AsyncSession, organization_id: UUID) -> None:
    await db.execute(
        text("SELECT set_config('app.organization_id', :org, true)"),
        {"org": str(organization_id)},
    )


@asynccontextmanager
async def tenant_transaction(organization_id: UUID) -> AsyncIterator[AsyncSession]:
    async with sessions.begin() as db:
        await set_tenant(db, organization_id)
        yield db


async def verify_database_role() -> None:
    async with engine.connect() as conn:
        role = (
            await conn.execute(
                text(
                    "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
        if role.rolname != "forge_app" or any(role[1:]):
            raise RuntimeError("Application requires restricted forge_app database role")
