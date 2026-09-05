from alembic import context
from sqlalchemy import create_engine

from forge_erp.core.config import settings

url = settings().migration_database_url
if not url:
    raise RuntimeError("MIGRATION_DATABASE_URL is required; never use application credentials")
if context.is_offline_mode():
    context.configure(url=url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url).connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
