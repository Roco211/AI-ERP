"""Isolated funds browser identities; business data is created through HTTP Commands."""

import json
import sys
from uuid import UUID, uuid4

from sqlalchemy import create_engine, text

from forge_erp.core.config import settings
from forge_erp.core.security import passwords

cfg = settings()
if cfg.app_env != "development":
    raise RuntimeError("Browser fixtures require development environment")

ROLE_PERMISSIONS = {
    "AR_READER": {"profile.read", "funds.ar.read"},
    "AP_READER": {"profile.read", "funds.ap.read"},
    "CASHIER": {"profile.read", "funds.ar.read", "funds.receive", "funds.customer_refund"},
    "PAYABLES": {"profile.read", "funds.ap.read", "funds.pay", "funds.supplier_refund"},
    "NO_FUNDS": {"profile.read"},
}

with create_engine(cfg.migration_database_url).begin() as db:
    if sys.argv[1] == "create":
        code = "BROWSER_FUNDS_" + uuid4().hex.upper()
        org = db.execute(
            text(
                "INSERT INTO forge.organizations(code,name) "
                "VALUES(:code,'Funds browser fixture') RETURNING id"
            ),
            {"code": code},
        ).scalar_one()
        password_hash = passwords.hash("funds-browser-fixture-only-9638")
        for role_code in ("ADMIN", *ROLE_PERMISSIONS):
            user = db.execute(
                text(
                    "INSERT INTO forge.users(organization_id,email,display_name,password_hash) "
                    "VALUES(:org,:email,:name,:hash) RETURNING id"
                ),
                {
                    "org": org,
                    "email": role_code.lower() + "@funds-browser.example.test",
                    "name": "Funds browser " + role_code,
                    "hash": password_hash,
                },
            ).scalar_one()
            role = db.execute(
                text(
                    "INSERT INTO forge.roles(organization_id,code,name) "
                    "VALUES(:org,:code,:code) RETURNING id"
                ),
                {"org": org, "code": role_code},
            ).scalar_one()
            db.execute(
                text("INSERT INTO forge.user_roles VALUES(:org,:user,:role)"),
                {"org": org, "user": user, "role": role},
            )
            permissions = (
                db.execute(text("SELECT code FROM forge.permissions")).scalars().all()
                if role_code == "ADMIN"
                else ROLE_PERMISSIONS[role_code]
            )
            for permission in permissions:
                db.execute(
                    text("INSERT INTO forge.role_permissions VALUES(:org,:role,:permission)"),
                    {"org": org, "role": role, "permission": permission},
                )
        print(json.dumps({"id": str(org), "code": code}))
    elif sys.argv[1] == "cleanup":
        org = UUID(sys.argv[2])
        code = db.execute(
            text("SELECT code FROM forge.organizations WHERE id=:org"), {"org": org}
        ).scalar_one()
        if not code.startswith("BROWSER_FUNDS_"):
            raise RuntimeError("Not a funds browser fixture organization")
        # Test-only cleanup of this isolated tenant; application roles keep all guards.
        db.execute(text("SET LOCAL session_replication_role = replica"))
        for table in (
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
