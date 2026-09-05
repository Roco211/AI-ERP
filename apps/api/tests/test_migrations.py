"""Exercise empty database and v0.4 upgrade paths on disposable, isolated databases."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from forge_erp.core.config import settings


@pytest.mark.parametrize("baseline", [None, "0001_bootstrap", "0004_product_embeddings"])
def test_clean_and_bootstrap_migrations(baseline):
    cfg = settings()
    name = "forge_migration_test_" + uuid4().hex
    root = Path(__file__).resolve().parents[3]
    admin = create_engine(cfg.migration_database_url, isolation_level="AUTOCOMMIT")
    url = make_url(cfg.migration_database_url).set(database=name)
    target = create_engine(url)
    try:
        with admin.connect() as db:
            db.execute(text(f'CREATE DATABASE "{name}"'))
        # Same prerequisites as infra/postgres init: role is cluster-wide, extensions per DB.
        with target.begin() as db:
            db.execute(text("CREATE EXTENSION vector"))
            db.execute(text("CREATE EXTENSION pg_trgm"))
        env = dict(os.environ, MIGRATION_DATABASE_URL=url.render_as_string(hide_password=False))
        for revision in [baseline, "head"] if baseline else ["head"]:
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "alembic",
                    "-c",
                    "apps/api/alembic.ini",
                    "upgrade",
                    revision,
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert run.returncode == 0, "Migration failed in disposable database"
        with target.connect() as db:
            assert (
                db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0005_inventory"
            )
            assert db.execute(text("SELECT count(*) FROM forge.products")).scalar_one() == 0
            assert db.execute(
                text(
                    "SELECT relforcerowsecurity FROM pg_class WHERE oid='forge.products'::regclass"
                )
            ).scalar_one()
    finally:
        target.dispose()
        with admin.connect() as db:
            db.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
