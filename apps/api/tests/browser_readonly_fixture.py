"""Ephemeral, empty organization for real read-only browser authorization tests."""

import json
import sys
from uuid import UUID, uuid4

from sqlalchemy import create_engine, text

from forge_erp.core.config import settings
from forge_erp.core.security import passwords

cfg = settings()
if cfg.app_env != "development":
    raise RuntimeError("Browser fixtures require development environment")
with create_engine(cfg.migration_database_url).begin() as db:
    if sys.argv[1] == "create":
        code = "BROWSER_INV_" + uuid4().hex.upper()
        org = db.execute(
            text(
                "INSERT INTO forge.organizations(code,name) VALUES (:code,'Browser read-only "
                "fixture') RETURNING id"
            ),
            {"code": code},
        ).scalar_one()
        user = db.execute(
            text(
                "INSERT INTO forge.users(organization_id,email,display_name,password_hash) "
                "VALUES (:org,'viewer@example.test','Browser Viewer',:hash) RETURNING id"
            ),
            {"org": org, "hash": passwords.hash("browser-fixture-password-8472")},
        ).scalar_one()
        role = db.execute(
            text(
                "INSERT INTO forge.roles(organization_id,code,name) VALUES "
                "(:org,'VIEWER','Read-only viewer') RETURNING id"
            ),
            {"org": org},
        ).scalar_one()
        db.execute(
            text("INSERT INTO forge.user_roles VALUES (:org,:user,:role)"),
            {"org": org, "user": user, "role": role},
        )
        for permission in ("profile.read", "inventory.read", "catalog.read", "warehouse.read"):
            db.execute(
                text("INSERT INTO forge.role_permissions VALUES (:org,:role,:permission)"),
                {"org": org, "role": role, "permission": permission},
            )
        print(json.dumps({"id": str(org), "code": code}))
    elif sys.argv[1] == "cleanup":
        org = UUID(sys.argv[2])
        code = db.execute(
            text("SELECT code FROM forge.organizations WHERE id=:org"), {"org": org}
        ).scalar_one()
        if not code.startswith("BROWSER_INV_"):
            raise RuntimeError("Not a browser fixture organization")
        for table in (
            "idempotency_keys",
            "outbox_events",
            "audit_events",
            "sessions",
            "role_permissions",
            "user_roles",
            "roles",
            "users",
        ):
            db.execute(text(f"DELETE FROM forge.{table} WHERE organization_id=:org"), {"org": org})
        db.execute(text("DELETE FROM forge.organizations WHERE id=:org"), {"org": org})
    else:
        raise ValueError("Unknown action")
