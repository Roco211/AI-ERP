"""Owned browser tenants and a scoped, removable import failure for real worker tests."""

import base64
import json
import sys
from uuid import UUID, uuid4

from sqlalchemy import create_engine, text

from forge_erp.core.config import settings
from forge_erp.core.security import passwords
from forge_erp.modules.catalog_import.infrastructure.xlsx import workbook_bytes

cfg = settings()
if cfg.app_env != "development":
    raise RuntimeError("Browser fixtures require development environment")


def guard(db, org):
    code = db.execute(
        text("SELECT code FROM forge.organizations WHERE id=:org"), {"org": org}
    ).scalar_one()
    if not code.startswith("BROWSER_OPERATIONS_"):
        raise RuntimeError("Not an operations browser fixture organization")


def trigger_name(org):
    return "browser_import_fail_" + org.hex


def release(db, org):
    guard(db, org)
    name = trigger_name(org)
    db.execute(text("SET LOCAL lock_timeout = '5s'"))
    db.execute(text(f"DROP TRIGGER IF EXISTS {name} ON forge.brands"))
    db.execute(text(f"DROP FUNCTION IF EXISTS forge.{name}()"))
    # The DDL guard must not time out normal Outbox consumers during fixture cleanup.
    db.execute(text("SET LOCAL lock_timeout = DEFAULT"))


def create(db):
    code = "BROWSER_OPERATIONS_" + uuid4().hex.upper()
    org = db.execute(
        text(
            "INSERT INTO forge.organizations(code,name) "
            "VALUES(:code,'Operations browser fixture') RETURNING id"
        ),
        {"code": code},
    ).scalar_one()
    password_hash = passwords.hash("operations-browser-fixture-only-4827")
    for name in ("ADMIN", "VIEWER"):
        user = db.execute(
            text(
                "INSERT INTO forge.users(organization_id,email,display_name,password_hash) "
                "VALUES(:org,:email,:name,:hash) RETURNING id"
            ),
            {
                "org": org,
                "email": name.lower() + "@operations-browser.example.test",
                "name": "Operations " + name,
                "hash": password_hash,
            },
        ).scalar_one()
        role = db.execute(
            text(
                "INSERT INTO forge.roles(organization_id,code,name) "
                "VALUES(:org,:code,:code) RETURNING id"
            ),
            {"org": org, "code": name},
        ).scalar_one()
        db.execute(
            text("INSERT INTO forge.user_roles VALUES(:org,:user,:role)"),
            {"org": org, "user": user, "role": role},
        )
        permissions = (
            db.execute(text("SELECT code FROM forge.permissions")).scalars().all()
            if name == "ADMIN"
            else {"profile.read", "dashboard.read", "sales.read", "inventory.read"}
        )
        for permission in permissions:
            db.execute(
                text("INSERT INTO forge.role_permissions VALUES(:org,:role,:permission)"),
                {"org": org, "role": role, "permission": permission},
            )
    return {"id": str(org), "code": code}


def cleanup(db, org):
    release(db, org)
    # Wait for any owned in-flight row before touching its Outbox/catalog facts.
    # New pollers skip these locks, so no further import rows can start during cleanup.
    db.execute(
        text("SELECT id FROM forge.import_rows WHERE organization_id=:org ORDER BY id FOR UPDATE"),
        {"org": org},
    ).all()
    db.execute(text("SET LOCAL session_replication_role = replica"))
    for table in (
        "outbox_events",
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


action = sys.argv[1]
if action in {"workbook", "workbook-large"}:
    print(
        base64.b64encode(
            workbook_bytes(
                ["编码", "名称", "备注"],
                [
                    ["IMPORT-PASS", "　浏览器导入品牌一　", "第一行应成功"],
                    ["IMPORT-RETRY", "浏览器导入品牌二", "仅此行会遇到临时失败"],
                ]
                if action == "workbook"
                else [
                    [f"LARGE-{index:05}", f"万行品牌{index}", "同源预览"] for index in range(10000)
                ],
            )
        ).decode()
    )
else:
    admin = create_engine(cfg.migration_database_url)
    try:
        with admin.begin() as db:
            if action == "create":
                result = create(db)
            else:
                org = UUID(sys.argv[2])
                guard(db, org)
                if action == "block-row":
                    name = trigger_name(org)
                    db.execute(text("SET LOCAL lock_timeout = '5s'"))
                    # UUID construction and a fixed code are the only interpolated literals.
                    db.execute(
                        text(f"""
                    CREATE FUNCTION forge.{name}() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN
                      IF NEW.organization_id='{org}'::uuid AND NEW.code='IMPORT-RETRY' THEN
                        RAISE EXCEPTION 'Scoped browser import fixture failure';
                      END IF;
                      RETURN NEW;
                    END $$;
                    REVOKE ALL ON FUNCTION forge.{name}() FROM PUBLIC;
                    CREATE TRIGGER {name} BEFORE INSERT ON forge.brands
                    FOR EACH ROW EXECUTE FUNCTION forge.{name}();
                    """)
                    )
                elif action == "release-row":
                    release(db, org)
                elif action == "cleanup":
                    cleanup(db, org)
                else:
                    raise ValueError("Unknown fixture action")
                result = {"action": action, "completed": True}
        print(json.dumps(result))
    finally:
        admin.dispose()
