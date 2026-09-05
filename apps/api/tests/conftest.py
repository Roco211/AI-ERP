from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text

from forge_erp.core.config import settings
from forge_erp.core.db import engine
from forge_erp.core.security import passwords
from forge_erp.main import app

TEST_PASSWORD = "test-only-password-8472"


@pytest.fixture(scope="session")
def password_hash():
    return passwords.hash(TEST_PASSWORD)


@pytest.fixture
def identities(password_hash):
    admin = create_engine(settings().migration_database_url)
    records = []
    with admin.begin() as db:
        for _ in range(2):
            code = "TEST_" + uuid4().hex.upper()
            org = db.execute(
                text(
                    "INSERT INTO forge.organizations(code,name) VALUES "
                    "(:code,'Test Organization') RETURNING id"
                ),
                {"code": code},
            ).scalar_one()
            user = db.execute(
                text(
                    "INSERT INTO forge.users(organization_id,email,display_name,"
                    "password_hash) VALUES (:org,'same@example.test','Test User',:hash) "
                    "RETURNING id"
                ),
                {"org": org, "hash": password_hash},
            ).scalar_one()
            role = db.execute(
                text(
                    "INSERT INTO forge.roles(organization_id,code,name) VALUES "
                    "(:org,'ADMIN','Admin') RETURNING id"
                ),
                {"org": org},
            ).scalar_one()
            db.execute(
                text("INSERT INTO forge.user_roles VALUES (:org,:user,:role)"),
                {"org": org, "user": user, "role": role},
            )
            db.execute(
                text("INSERT INTO forge.role_permissions VALUES (:org,:role,'profile.read')"),
                {"org": org, "role": role},
            )
            records.append({"org": org, "user": user, "role": role, "code": code})
    yield records
    with admin.begin() as db:
        # Isolated TEST_* cleanup only; immutable facts remain protected for forge_app.
        db.execute(text("SET LOCAL session_replication_role = replica"))
        for rec in records:
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
                db.execute(text(f"DELETE FROM forge.{table} WHERE organization_id=:org"), rec)
            db.execute(text("DELETE FROM forge.organizations WHERE id=:org"), rec)
    admin.dispose()


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app, client=(uuid4().hex, 1234))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Origin": settings().web_origin}
    ) as client:
        yield client
    await engine.dispose()


@pytest.fixture(autouse=True)
async def dispose_pool():
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
def disable_live_model_in_tests(monkeypatch):
    # Real model behavior is covered by the separate semantic eval command.
    monkeypatch.setattr(settings(), "embedding_enabled", False)
