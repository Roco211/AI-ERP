import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions, set_tenant
from forge_erp.core.errors import Problem
from forge_erp.modules.catalog.application.service import write_command


@pytest.fixture
async def catalog_client(client, identities):
    with create_engine(settings().migration_database_url).begin() as db:
        for identity in identities:
            db.execute(
                text(
                    "INSERT INTO forge.role_permissions "
                    "SELECT :org,:role,code FROM forge.permissions ON CONFLICT DO NOTHING"
                ),
                identity,
            )
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "organization_code": identities[0]["code"],
            "email": "same@example.test",
            "password": "test-only-password-8472",
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 200
    return client


async def create(client, path, body, key=None):
    return await client.post(
        "/api/v1/" + path, json=body, headers={"Idempotency-Key": key or uuid4().hex}
    )


async def test_dictionary_lifecycle(catalog_client):
    c = catalog_client
    key = uuid4().hex
    body = {"code": "BOLT", "name": "紧固件", "attribute_schema": []}
    first = await create(c, "categories", body, key)
    assert first.status_code == 201, first.text
    record = first.json()
    again = await create(c, "categories", body, key)
    assert again.json() == record
    conflict = await create(c, "categories", body | {"name": "不同名称"}, key)
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert (await create(c, "categories", body)).status_code == 409
    assert (await c.get("/api/v1/categories?q=紧固")).json()["total"] == 1
    update = await c.put(
        "/api/v1/categories/" + record["id"],
        json=body | {"name": "紧固件分类", "expected_version": 1},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert update.status_code == 200 and update.json()["version"] == 2
    stale = await c.put(
        "/api/v1/categories/" + record["id"],
        json=body | {"expected_version": 1},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert stale.json()["code"] == "DOCUMENT_VERSION_CONFLICT"
    off = await create(c, "categories/" + record["id"] + "/deactivate", {"expected_version": 2})
    assert off.status_code == 200 and not off.json()["active"]
    assert (await c.get("/api/v1/categories?active=true")).json()["total"] == 0
    assert (await create(c, "brands", {"code": "B", "name": "品牌"})).status_code == 201
    assert (await create(c, "units", {"code": "PCS", "name": "个"})).status_code == 201


async def test_category_cycle_and_tenant_reference(catalog_client, identities):
    c = catalog_client
    a = (await create(c, "categories", {"code": "A", "name": "A"})).json()
    b = (await create(c, "categories", {"code": "B", "name": "B", "parent_id": a["id"]})).json()
    response = await c.put(
        "/api/v1/categories/" + a["id"],
        json={"code": "A", "name": "A", "parent_id": b["id"], "expected_version": 1},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.json()["code"] == "CATEGORY_CYCLE"
    other = identities[1]
    async with sessions.begin() as db:
        await set_tenant(db, other["org"])
        row = await write_command(
            db,
            RuntimeContext(other["org"], other["user"], frozenset({"catalog.write"}), "test"),
            "categories",
            {
                "code": "OTHER",
                "name": "Other",
                "notes": "",
                "parent_id": None,
                "attribute_schema": [],
            },
            uuid4().hex,
        )
    assert (await c.get("/api/v1/categories/" + row["id"])).status_code == 404
    bad = await create(c, "categories", {"code": "C", "name": "C", "parent_id": row["id"]})
    assert bad.status_code == 409 and bad.json()["code"] == "INVALID_REFERENCE"


async def test_dictionary_permissions_and_audit(catalog_client, identities):
    c = catalog_client
    r = await create(c, "units", {"code": "BOX", "name": "箱"})
    assert r.status_code == 201
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        audit = (
            await db.execute(
                text("SELECT after FROM forge.audit_events WHERE action='catalog.units.create'")
            )
        ).scalar_one()
        assert audit["name"] == "箱"
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.outbox_events WHERE "
                    "event_type='catalog.units.create'"
                )
            )
        ).scalar_one() == 1
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                "AND permission_code='catalog.write'"
            ),
            identities[0],
        )
    assert (await create(c, "brands", {"code": "NO", "name": "No"})).status_code == 403


async def test_concurrent_dictionary_edit(identities):
    a = identities[0]
    ctx = RuntimeContext(a["org"], a["user"], frozenset({"catalog.write"}), "concurrency")
    async with sessions.begin() as db:
        await set_tenant(db, a["org"])
        row = await write_command(
            db, ctx, "brands", {"code": "A", "name": "A", "notes": ""}, uuid4().hex
        )

    async def edit(name):
        async with sessions.begin() as db:
            await set_tenant(db, a["org"])
            return await write_command(
                db,
                ctx,
                "brands",
                {"code": "A", "name": name, "notes": ""},
                uuid4().hex,
                UUID(row["id"]),
                1,
            )

    outcomes = await asyncio.gather(edit("One"), edit("Two"), return_exceptions=True)
    assert (
        sum(isinstance(r, Problem) and r.code == "DOCUMENT_VERSION_CONFLICT" for r in outcomes) == 1
    )


async def product_fixture(c):
    category = (
        await create(
            c,
            "categories",
            {
                "code": "FASTENER",
                "name": "紧固件",
                "attribute_schema": [
                    {
                        "key": "材质",
                        "label": "材质",
                        "kind": "enum",
                        "required": True,
                        "options": ["304", "316"],
                    },
                    {"key": "长度", "label": "长度", "kind": "decimal"},
                ],
            },
        )
    ).json()
    unit = (await create(c, "units", {"code": "PCS", "name": "个"})).json()
    body = {
        "sku": "BOLT-M8-30",
        "name": "304不锈钢外六角螺栓 M8×30",
        "category_id": category["id"],
        "base_unit_id": unit["id"],
        "attributes": {"材质": "304", "长度": "30"},
        "specification": "M8×30",
    }
    response = await create(c, "products", body)
    assert response.status_code == 201, response.text
    return response.json(), body, category, unit


async def test_product_attributes_and_base_unit(catalog_client):
    c = catalog_client
    p, body, category, unit = await product_fixture(c)
    assert p["default_sales_unit_id"] == unit["id"]
    units = (await c.get("/api/v1/product-units", params={"product_id": p["id"]})).json()["items"]
    assert len(units) == 1 and units[0]["unit_to_base_factor"] == "1.000000"
    for attributes in (
        {},
        {"材质": "铜"},
        {"材质": "304", "长度": "NaN"},
        {"材质": "304", "未知": "x"},
    ):
        bad = await create(c, "products", body | {"sku": uuid4().hex, "attributes": attributes})
        assert bad.status_code == 422, bad.text
    bad = await create(c, "products", body | {"sku": "FLOAT", "min_stock_qty": 0.1})
    assert bad.status_code == 422
    box = (await create(c, "units", {"code": "BOX", "name": "箱"})).json()
    changed = await c.put(
        "/api/v1/products/" + p["id"],
        json=body | {"base_unit_id": box["id"], "expected_version": 1},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert changed.json()["code"] == "BASE_UNIT_IMMUTABLE"
    incompatible = await c.put(
        "/api/v1/categories/" + category["id"],
        json={"code": "FASTENER", "name": "紧固件", "attribute_schema": [], "expected_version": 1},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert incompatible.status_code == 422
    off = await create(
        c, "product-units/" + units[0]["id"] + "/deactivate", {"expected_version": 1}
    )
    assert off.json()["code"] == "UNIT_IN_USE"


async def test_prices_contacts_and_permissions(catalog_client, identities):
    c = catalog_client
    p, _, _, unit = await product_fixture(c)
    customer = (
        await create(c, "customers", {"code": "C1", "name": "客户", "price_tier": "wholesale"})
    ).json()
    supplier = (await create(c, "suppliers", {"code": "S1", "name": "供应商"})).json()
    assert (await create(c, "warehouses", {"code": "WH1", "name": "主仓"})).status_code == 201
    price = await create(
        c, "product-prices", {"product_id": p["id"], "price_type": "standard", "price": "2.123456"}
    )
    assert price.status_code == 201, price.text
    assert (await c.get("/api/v1/products/" + p["id"])).json()["standard_price"] == "2.123456"
    assert (
        await create(
            c,
            "product-prices",
            {
                "product_id": p["id"],
                "price_type": "customer",
                "customer_id": customer["id"],
                "price": "1.5",
            },
        )
    ).status_code == 201
    sp = await create(
        c,
        "supplier-products",
        {
            "product_id": p["id"],
            "supplier_id": supplier["id"],
            "supplier_sku": "S-BOLT",
            "purchase_unit_id": unit["id"],
        },
    )
    assert sp.status_code == 201, sp.text
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                "AND permission_code='product.price.read'"
            ),
            identities[0],
        )
    assert (await c.get("/api/v1/product-prices")).status_code == 403
    assert (await c.get("/api/v1/products/" + p["id"])).json()["standard_price"] is None


def test_conversion_snapshot_exact_and_frozen():
    from decimal import Decimal

    from pydantic import ValidationError

    from forge_erp.modules.catalog.domain.values import ConversionSnapshot

    snap = ConversionSnapshot.capture(uuid4(), uuid4(), Decimal("2.5"), Decimal("1000"), 1)
    assert snap.base_qty == Decimal("2500")
    with pytest.raises(ValidationError):
        snap.qty = Decimal(3)
    with pytest.raises(ValidationError):
        ConversionSnapshot.capture(uuid4(), uuid4(), Decimal("0.000001"), Decimal("0.000001"), 1)
    with pytest.raises(ValidationError):
        ConversionSnapshot.capture(
            uuid4(), uuid4(), Decimal("99999999999999"), Decimal("99999999999999"), 1
        )


async def test_search_snapshot_and_price_resolution(catalog_client):
    c = catalog_client
    p, body, _, unit = await product_fixture(c)
    box = (await create(c, "units", {"code": "BOX", "name": "箱"})).json()
    conversion = (
        await create(
            c,
            "product-units",
            {"product_id": p["id"], "unit_id": box["id"], "unit_to_base_factor": "1000"},
        )
    ).json()
    query = {"product_id": p["id"], "unit_id": box["id"], "qty": "2.5"}
    snap = await c.get("/api/v1/catalog/conversion", params=query)
    assert snap.status_code == 200, snap.text
    assert snap.json()["base_qty"] == "2500.0000000"
    changed = await c.put(
        "/api/v1/product-units/" + conversion["id"],
        json={
            "product_id": p["id"],
            "unit_id": box["id"],
            "unit_to_base_factor": "500",
            "expected_version": 1,
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert changed.status_code == 200, changed.text
    next_snap = await c.get("/api/v1/catalog/conversion", params=query)
    from decimal import Decimal

    assert Decimal(next_snap.json()["base_qty"]) == 1250
    assert Decimal(snap.json()["base_qty"]) == 2500
    for q in ("BOLT-M8-30", "304 8*30", "M8×30"):
        result = await c.get("/api/v1/catalog/search", params={"q": q})
        assert result.status_code == 200, result.text
        assert result.json()["items"][0]["id"] == p["id"]
    assert (
        await c.get(
            "/api/v1/catalog/search", params={"attribute_key": "材质", "attribute_value": "316"}
        )
    ).json()["total"] == 0
    assert (await c.get("/api/v1/catalog/search", params={"q": "%_' OR 1=1 --"})).json()[
        "total"
    ] == 0
    customer = (
        await create(c, "customers", {"code": "VIP", "name": "客户", "price_tier": "wholesale"})
    ).json()
    for tier, price in [("standard", "2"), ("wholesale", "1.5")]:
        assert (
            await create(
                c, "product-prices", {"product_id": p["id"], "price_type": tier, "price": price}
            )
        ).status_code == 201
    price = (
        await c.get(
            "/api/v1/catalog/price", params={"product_id": p["id"], "customer_id": customer["id"]}
        )
    ).json()
    assert price["source"] == "wholesale" and Decimal(price["price"]) == Decimal("1.5")
    await create(
        c,
        "product-prices",
        {
            "product_id": p["id"],
            "price_type": "customer",
            "customer_id": customer["id"],
            "price": "1.2",
        },
    )
    assert (
        await c.get(
            "/api/v1/catalog/price", params={"product_id": p["id"], "customer_id": customer["id"]}
        )
    ).json()["source"] == "customer"


async def test_all_catalog_rls_and_foreign_keys(catalog_client, identities):
    c = catalog_client
    p, body, _, _ = await product_fixture(c)
    other = identities[1]
    from sqlalchemy.exc import DBAPIError

    from forge_erp.modules.catalog.infrastructure.resources import RESOURCES

    async with sessions.begin() as db:
        await set_tenant(db, other["org"])
        for spec in RESOURCES.values():
            assert (
                await db.execute(
                    text(f"SELECT count(*) FROM forge.{spec.table} WHERE organization_id=:org"),
                    {"org": identities[0]["org"]},
                )
            ).scalar_one() == 0
            assert (
                await db.execute(
                    text(
                        f"UPDATE forge.{spec.table} SET notes='leak' "
                        "WHERE organization_id=:org RETURNING id"
                    ),
                    {"org": identities[0]["org"]},
                )
            ).first() is None
    async with sessions.begin() as db:
        await set_tenant(db, other["org"])
        with pytest.raises(DBAPIError):
            await db.execute(
                text(
                    "INSERT INTO forge.warehouses(organization_id,code,name) VALUES (:org,'X','X')"
                ),
                {"org": identities[0]["org"]},
            )
    async with sessions.begin() as db:
        await set_tenant(db, other["org"])
        with pytest.raises(DBAPIError):
            await db.execute(
                text(
                    "INSERT INTO forge.product_prices(organization_id,product_id,price_type,price) "
                    "VALUES (:org,:product,'standard',1)"
                ),
                {"org": other["org"], "product": p["id"]},
            )
