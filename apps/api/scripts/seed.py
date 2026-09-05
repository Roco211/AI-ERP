"""Explicit development-only seed; elevated connection, environment credentials."""

import os

from sqlalchemy import create_engine, text

from forge_erp.core.config import settings
from forge_erp.core.security import passwords


def seed() -> None:
    cfg = settings()
    if cfg.app_env != "development":
        raise RuntimeError("Dev seed is disabled outside development")
    # Settings loads the root env; read seed values without exposing them.
    from pathlib import Path

    from dotenv import dotenv_values

    values = {**dotenv_values(Path(__file__).resolve().parents[3] / ".env"), **os.environ}
    email, password = values.get("SEED_ADMIN_EMAIL"), values.get("SEED_ADMIN_PASSWORD")
    if not email or not password or len(password) < 12:
        raise RuntimeError("Set SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD (12+ characters)")
    with create_engine(cfg.migration_database_url).begin() as db:
        org = db.execute(
            text(
                "INSERT INTO forge.organizations(code,name) VALUES ('DEMO',"
                "'Forge Demo') ON CONFLICT(code) DO UPDATE SET code=EXCLUDED.code "
                "RETURNING id"
            )
        ).scalar_one()
        user = db.execute(
            text(
                "INSERT INTO forge.users(organization_id,email,display_name,"
                "password_hash) VALUES (:org,:email,'Demo Administrator',:hash) "
                "ON CONFLICT(organization_id,email) DO UPDATE SET email=EXCLUDED.email RETURNING id"
            ),
            {"org": org, "email": email.lower(), "hash": passwords.hash(password)},
        ).scalar_one()
        role = db.execute(
            text(
                "INSERT INTO forge.roles(organization_id,code,name) VALUES "
                "(:org,'ADMIN','Administrator') ON CONFLICT(organization_id,code) "
                "DO UPDATE SET code=EXCLUDED.code RETURNING id"
            ),
            {"org": org},
        ).scalar_one()
        db.execute(
            text("INSERT INTO forge.user_roles VALUES (:org,:user,:role) ON CONFLICT DO NOTHING"),
            {"org": org, "user": user, "role": role},
        )
        db.execute(
            text(
                "INSERT INTO forge.role_permissions SELECT :org,:role,code "
                "FROM forge.permissions ON CONFLICT DO NOTHING"
            ),
            {"org": org, "role": role},
        )
    print("DEMO organization and ADMIN identity seeded; existing password kept unchanged.")


if __name__ == "__main__":
    seed()
