"""Create and remove owned identities for the real HTTP/browser assistant tests."""

import json
import sys
from uuid import UUID, uuid4

from sqlalchemy import create_engine, text

from forge_erp.core.config import settings
from forge_erp.core.security import passwords

cfg = settings()
if cfg.app_env != "development":
    raise RuntimeError("Browser fixtures require development environment")


def create(db):
    code = "BROWSER_ASSISTANT_" + uuid4().hex.upper()
    org = db.execute(
        text(
            "INSERT INTO forge.organizations(code,name) "
            "VALUES(:code,'Assistant browser fixture') RETURNING id"
        ),
        {"code": code},
    ).scalar_one()
    password_hash = passwords.hash("assistant-browser-fixture-only-4827")
    for name in ("ADMIN", "VIEWER"):
        user = db.execute(
            text(
                "INSERT INTO forge.users(organization_id,email,display_name,password_hash) "
                "VALUES(:org,:email,:name,:hash) RETURNING id"
            ),
            {
                "org": org,
                "email": name.lower() + "@assistant-browser.example.test",
                "name": "Assistant " + name,
                "hash": password_hash,
            },
        ).scalar_one()
        role = db.execute(
            text(
                "INSERT INTO forge.roles(organization_id,code,name) "
                "VALUES(:org,:name,:name) RETURNING id"
            ),
            {"org": org, "name": name},
        ).scalar_one()
        db.execute(
            text("INSERT INTO forge.user_roles VALUES(:org,:user,:role)"),
            {"org": org, "user": user, "role": role},
        )
        permissions = (
            db.execute(text("SELECT code FROM forge.permissions")).scalars().all()
            if name == "ADMIN"
            else {"profile.read", "ai.use", "inventory.read", "dashboard.read"}
        )
        for permission in permissions:
            db.execute(
                text("INSERT INTO forge.role_permissions VALUES(:org,:role,:permission)"),
                {"org": org, "role": role, "permission": permission},
            )
    return {"id": str(org), "code": code}


def cleanup(db, org):
    code = db.execute(
        text("SELECT code FROM forge.organizations WHERE id=:org"), {"org": org}
    ).scalar_one()
    if not code.startswith("BROWSER_ASSISTANT_"):
        raise RuntimeError("Not an assistant browser fixture organization")
    db.execute(text("SET LOCAL session_replication_role = replica"))
    # Follow the same Outbox-first cleanup lock order as integration fixtures.
    for table in (
        "outbox_events",
        "assistant_checkpoint_writes",
        "assistant_checkpoints",
        "assistant_draft_receipts",
        "assistant_proposals",
        "assistant_turns",
        "assistant_conversations",
        "ai_provider_settings",
        "import_rows",
        "import_batches",
        "replenishment_creations",
        "funds_operations",
        "funds_cash_reversals",
        "funds_cash_allocations",
        "funds_cash_documents",
        "funds_entries",
        "funds_sources",
        "funds_activation",
        "sales_document_lines",
        "sales_documents",
        "sales_order_lines",
        "sales_orders",
        "purchase_document_lines",
        "purchase_documents",
        "purchase_order_lines",
        "purchase_orders",
        "inventory_movements",
        "inventory_reversals",
        "inventory_reservations",
        "inventory_document_lines",
        "inventory_documents",
        "inventory_balances",
        "product_embeddings",
        "supplier_products",
        "product_prices",
        "product_units",
        "products",
        "customers",
        "suppliers",
        "warehouses",
        "categories",
        "brands",
        "units",
        "idempotency_keys",
        "audit_events",
        "sessions",
        "role_permissions",
        "user_roles",
        "roles",
        "users",
    ):
        db.execute(text(f"DELETE FROM forge.{table} WHERE organization_id=:org"), {"org": org})
    db.execute(text("DELETE FROM forge.organizations WHERE id=:org"), {"org": org})


admin = create_engine(cfg.migration_database_url)
try:
    with admin.begin() as db:
        if sys.argv[1] == "create":
            result = create(db)
        elif sys.argv[1] == "cleanup":
            cleanup(db, UUID(sys.argv[2]))
            result = {"completed": True}
        else:
            raise ValueError("Unknown fixture action")
    print(json.dumps(result))
finally:
    admin.dispose()
