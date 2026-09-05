import asyncio
import json
from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.util.concurrency import await_only
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_sales_orders import action, balance, detail, opening, so
from test_sales_orders import sale as sale
from test_sales_shipments import post_shipment, reconcile_sale, shipment, stock_document

from forge_erp.core.config import settings
from forge_erp.core.db import sessions, set_tenant
from forge_erp.main import app
from forge_erp.modules.inventory.application.engine import InventoryEngine
from forge_erp.workers.outbox import drain_outbox


@pytest.mark.parametrize(
    "marker",
    [
        "INSERT INTO forge.inventory_movements",
        "UPDATE forge.inventory_balances",
        "UPDATE forge.inventory_reservations",
        "UPDATE forge.inventory_documents",
        "INSERT INTO forge.audit_events",
        "INSERT INTO forge.outbox_events",
        "INSERT INTO forge.idempotency_keys",
        "COMMIT",
    ],
)
async def test_shipment_failures_roll_back_facts_and_allow_original_key_retry(
    catalog_client, sale, identities, monkeypatch, marker
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    row, _ = await shipment(c, order)
    before = await balance(c, sale)
    original = AsyncSession.execute
    key = uuid4().hex
    injected = False

    async def broken(self, statement, *args, **kwargs):
        nonlocal injected
        result = await original(self, statement, *args, **kwargs)
        if marker in str(statement) and not injected:
            injected = True
            raise RuntimeError("injected shipment failure")
        return result

    def fail_commit(session):
        nonlocal injected
        injected = True
        raise RuntimeError("injected shipment commit failure")

    if marker == "COMMIT":
        event.listen(Session, "before_commit", fail_commit)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "execute", broken)
            response = await post_shipment(c, row, key)
    finally:
        if marker == "COMMIT":
            event.remove(Session, "before_commit", fail_commit)
    assert injected and response.status_code == 500, response.text
    assert "injected" not in response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert await balance(c, sale) == before
    assert (await stock_document(c, row))["status"] == "DRAFT"
    assert D((await detail(c, order))["lines"][0]["shipped_base_qty"]) == 0
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        for table, where in [
            ("inventory_movements", "kind='ISSUE'"),
            ("audit_events", "action='sales.document.post'"),
            ("outbox_events", "event_type='sales.document.post'"),
        ]:
            count = await db.execute(text(f"SELECT count(*) FROM forge.{table} WHERE {where}"))
            assert count.scalar_one() == 0
    assert (await post_shipment(c, row, key)).status_code == 200
    assert D((await detail(c, order))["lines"][0]["shipped_base_qty"]) == 3
    await reconcile_sale(sale, identities[0])


async def test_multiline_shipment_second_issue_failure_rolls_back_all_lines(
    catalog_client, sale, identities, monkeypatch
):
    c = catalog_client
    original_product = (await c.get("/api/v1/products/" + sale["lines"][0]["product_id"])).json()
    second = (
        await create(
            c,
            "products",
            {
                "sku": "SECOND-SHIP",
                "name": "销售第二行",
                "category_id": original_product["category_id"],
                "attributes": original_product["attributes"],
                "base_unit_id": sale["lines"][0]["unit_id"],
            },
        )
    ).json()
    sale = sale | {"lines": sale["lines"] + [sale["lines"][0] | {"product_id": second["id"]}]}
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    source = await detail(c, order)
    draft = await create(
        c,
        "sales/shipments",
        {
            "source_id": order["id"],
            "reason": "两行原子性",
            "lines": [{"source_line_id": x["id"], "qty": "3"} for x in source["lines"]],
        },
    )
    assert draft.status_code == 201, draft.text
    row = draft.json()
    before = await balance(c, sale)
    original = InventoryEngine.change
    calls = 0

    async def broken(self, *args, **kwargs):
        nonlocal calls
        result = await original(self, *args, **kwargs)
        calls += 1
        if calls == 2:
            raise RuntimeError("second shipment issue failure")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(InventoryEngine, "change", broken)
        assert (await post_shipment(c, row)).status_code == 500
    assert calls == 2
    assert await balance(c, sale) == before
    assert all(D(x["shipped_base_qty"]) == 0 for x in (await detail(c, order))["lines"])
    assert (await post_shipment(c, row)).status_code == 200
    assert all(D(x["shipped_base_qty"]) == 3 for x in (await detail(c, order))["lines"])
    await reconcile_sale(sale, identities[0])


async def test_shipment_total_amount_overflow_rejects_every_draft_line(
    catalog_client, sale, identities
):
    c = catalog_client
    original_product = (await c.get("/api/v1/products/" + sale["lines"][0]["product_id"])).json()
    second = (
        await create(
            c,
            "products",
            {
                "sku": "TOTAL-OVERFLOW",
                "name": "金额上限第二商品",
                "category_id": original_product["category_id"],
                "attributes": original_product["attributes"],
                "base_unit_id": sale["lines"][0]["unit_id"],
            },
        )
    ).json()
    line = sale["lines"][0] | {"qty": "1", "unit_price": "100000000000"}
    sale = sale | {"lines": [line, line | {"product_id": second["id"]}]}
    await opening(c, sale, "1")
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    before = await balance(c, sale)
    source = await detail(c, order)
    response = await create(
        c,
        "sales/shipments",
        {
            "source_id": order["id"],
            "reason": "单行金额合法但整单金额溢出",
            "lines": [{"source_line_id": x["id"], "qty": "99999"} for x in source["lines"]],
        },
    )
    assert response.status_code == 422 and response.json()["code"] == "NUMERIC_OVERFLOW"
    assert await balance(c, sale) == before
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.sales_documents WHERE kind='SHIPMENT'")
            )
        ).scalar_one() == 0
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.audit_events WHERE action='sales.document.create'")
            )
        ).scalar_one() == 0


@pytest.mark.parametrize("removed", ["cost", "price", "both"])
async def test_shipment_cost_and_price_redaction_are_independent(
    catalog_client, sale, identities, removed
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    row, body = await shipment(c, order)
    permissions = {
        "cost": ["product.cost.read"],
        "price": ["product.price.read"],
        "both": ["product.cost.read", "product.price.read"],
    }[removed]
    with create_engine(settings().migration_database_url).begin() as db:
        for permission in permissions:
            db.execute(
                text(
                    "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                    "AND permission_code=:permission"
                ),
                identities[0] | {"permission": permission},
            )
    # Quantity-only warehouse staff can create/edit/post without financial read permissions.
    extra = await create(c, "sales/shipments", body)
    assert extra.status_code == 201, extra.text
    updated = await c.put(
        "/api/v1/sales/documents/" + row["id"] + "/draft",
        headers={"Idempotency-Key": uuid4().hex},
        json=body | {"expected_version": row["version"]},
    )
    assert updated.status_code == 200, updated.text
    posted = await post_shipment(c, updated.json())
    assert posted.status_code == 200, posted.text
    assert set(posted.json()) == {"id", "status", "version", "request_id"}
    for path in ["orders", "orders/" + order["id"], "documents", "documents/" + row["id"]]:
        response = await c.get("/api/v1/sales/" + path)
        assert response.status_code == 200, response.text
        for forbidden in [
            "actual_cost",
            "inventory_value_delta",
            "input_unit_cost",
            "gross_margin",
        ]:
            assert forbidden not in response.text
        if removed != "cost":
            for forbidden in ["unit_price", '"amount"', "price_source", "pricing_mode"]:
                assert forbidden not in response.text
    if removed == "cost":
        line = (await stock_document(c, row))["lines"][0]
        assert D(line["unit_price"]) == 15 and D(line["amount"]) == 45


async def test_sales_ship_permission_does_not_grant_sales_read(catalog_client, sale, identities):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    _, body = await shipment(c, order)
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                "AND permission_code NOT IN ('sales.ship','profile.read')"
            ),
            identities[0],
        )
    response = await create(c, "sales/shipments", body)
    assert response.status_code == 201, response.text
    row = response.json()
    assert (await post_shipment(c, row)).status_code == 200
    for path in ["orders", "orders/" + order["id"], "documents", "documents/" + row["id"]]:
        assert (await c.get("/api/v1/sales/" + path)).status_code == 403


async def test_revoked_shipment_permission_blocks_original_idempotency_keys(
    catalog_client, sale, identities
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    _, body = await shipment(c, order)
    create_key, post_key = uuid4().hex, uuid4().hex
    created = await create(c, "sales/shipments", body, create_key)
    assert created.status_code == 201, created.text
    row = created.json()
    posted = await post_shipment(c, row, post_key)
    assert posted.status_code == 200, posted.text
    assert (await post_shipment(c, row, post_key)).json() == posted.json()
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                "AND permission_code='sales.ship'"
            ),
            identities[0],
        )
    assert (await create(c, "sales/shipments", body, create_key)).status_code == 403
    assert (await post_shipment(c, row, post_key)).status_code == 403
    response = await c.put(
        "/api/v1/sales/documents/" + row["id"] + "/draft",
        headers={"Idempotency-Key": uuid4().hex},
        json=body | {"expected_version": row["version"]},
    )
    assert response.status_code == 403


async def test_shipment_source_input_and_generic_command_guards(catalog_client, sale, identities):
    c = catalog_client
    await opening(c, sale, "20")
    first, second = await so(c, sale), await so(c, sale)
    assert (await action(c, first)).status_code == 200
    assert (await action(c, second)).status_code == 200
    row, body = await shipment(c, first)
    wrong_line = (await detail(c, second))["lines"][0]["id"]
    bad = await create(
        c, "sales/shipments", body | {"lines": [{"source_line_id": wrong_line, "qty": "1"}]}
    )
    assert bad.status_code == 404
    replaced = await c.put(
        "/api/v1/sales/documents/" + row["id"] + "/draft",
        headers={"Idempotency-Key": uuid4().hex},
        json=body | {"source_id": second["id"], "expected_version": row["version"]},
    )
    assert replaced.status_code == 409 and replaced.json()["code"] == "INVALID_SOURCE"
    for field, value in [
        ("unit_price", "1"),
        ("actual_cost", "1"),
        ("reservation_id", str(uuid4())),
        ("reservation_source_line_id", str(uuid4())),
        ("warehouse_id", sale["warehouse_id"]),
        ("organization_id", str(identities[1]["org"])),
    ]:
        bad = await create(
            c, "sales/shipments", body | {"lines": [body["lines"][0] | {field: value}]}
        )
        assert bad.status_code == 422, (field, bad.text)
    assert (
        await create(c, "sales/shipments", body | {"organization_id": str(identities[1]["org"])})
    ).status_code == 422
    assert (
        await create(c, "sales/shipments", body | {"lines": body["lines"] * 2})
    ).status_code == 422
    assert (
        await create(c, "sales/shipments", body | {"lines": body["lines"] * 201})
    ).status_code == 422
    for command in ["post", "reverse"]:
        bad = await create(
            c,
            f"inventory/documents/{row['id']}/{command}",
            {"expected_version": row["version"]}
            | ({"reason": "绕过"} if command == "reverse" else {}),
        )
        assert bad.status_code == 409 and bad.json()["code"] == "SALES_COMMAND_REQUIRED"
    assert (
        await create(c, "purchasing/documents/" + row["id"] + "/post", {"expected_version": 1})
    ).status_code == 404
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        internal = str(
            (
                await db.execute(
                    text("SELECT id FROM forge.sales_documents WHERE kind='RESERVATION' LIMIT 1")
                )
            ).scalar_one()
        )
    assert (await c.get("/api/v1/sales/documents/" + internal)).status_code == 404
    assert (await post_shipment(c, {"id": internal, "version": 2})).status_code == 404
    listing = await c.get("/api/v1/sales/documents?page_size=1")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["kind"] == "SHIPMENT"
    assert (await c.get("/api/v1/sales/documents?page_size=101")).status_code == 422


async def test_posted_shipment_sql_immutability_and_tenant_isolation(
    catalog_client, sale, identities
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    row, body = await shipment(c, order)
    assert (await post_shipment(c, row)).status_code == 200
    for sql in [
        "UPDATE forge.sales_document_lines SET amount=0 WHERE document_id=:id",
        "DELETE FROM forge.sales_document_lines WHERE document_id=:id",
        "UPDATE forge.inventory_document_lines SET qty=1,base_qty=1 WHERE document_id=:id",
        "UPDATE forge.inventory_documents SET reason='changed' WHERE id=:id",
    ]:
        with pytest.raises(DBAPIError):
            async with sessions.begin() as db:
                await set_tenant(db, identities[0]["org"])
                await db.execute(text(sql), {"id": row["id"]})
    for org in [identities[1]["org"], None]:
        async with sessions.begin() as db:
            if org:
                await set_tenant(db, org)
            for table in ["sales_documents", "sales_document_lines", "inventory_reservations"]:
                assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar() == 0
    await c.post("/api/v1/auth/logout", json={})
    login = await create(
        c,
        "auth/login",
        {
            "organization_code": identities[1]["code"],
            "email": "same@example.test",
            "password": "test-only-password-8472",
        },
    )
    assert login.status_code == 200, login.text
    assert (await c.get("/api/v1/sales/documents/" + row["id"])).status_code == 404
    assert (await post_shipment(c, row)).status_code == 404
    assert (await create(c, "sales/shipments", body)).status_code == 404
    assert (await c.get("/api/v1/sales/documents")).json()["items"] == []


async def test_shipment_audit_outbox_and_redis_independence(
    catalog_client, sale, identities, monkeypatch
):
    from redis.asyncio import Redis

    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    row, _ = await shipment(c, order)

    async def unavailable(*args, **kwargs):
        raise ConnectionError("Redis unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(Redis, "execute_command", unavailable)
        response = await post_shipment(c, row)
    assert response.status_code == 200, response.text
    before = await balance(c, sale)
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        audit = (
            await db.execute(
                text(
                    "SELECT before,after FROM forge.audit_events "
                    "WHERE action='sales.document.post' AND resource_id=:id"
                ),
                {"id": row["id"]},
            )
        ).one()
        assert audit.before["document"]["status"] == "DRAFT"
        assert audit.after["document"]["status"] == "POSTED"
        assert D(audit.after["lines"][0]["actual_cost"]) == 33
        event_row = (
            await db.execute(
                text(
                    "SELECT payload,processed_at FROM forge.outbox_events "
                    "WHERE event_type='sales.document.post'"
                )
            )
        ).one()
        assert set(event_row.payload) <= {"resource_id", "resource_type", "version"}
        assert event_row.processed_at is None
    await drain_outbox(identities[0]["org"])
    await drain_outbox(identities[0]["org"])
    assert await balance(c, sale) == before
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.inventory_movements WHERE kind='ISSUE'")
            )
        ).scalar_one() == 1


async def test_shipment_success_headers_wait_for_committed_issue(catalog_client, sale, identities):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    row, _ = await shipment(c, order)
    path = "/api/v1/sales/documents/" + row["id"] + "/post"
    body = {"expected_version": row["version"]}
    observations = []
    delivered = False

    async def receive_message():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.start":
            assert message["status"] == 200
            async with sessions.begin() as db:
                await set_tenant(db, identities[0]["org"])
                observations.append(
                    (
                        await db.execute(
                            text(
                                "SELECT count(*) FROM forge.inventory_movements "
                                "WHERE operation_id=:id AND kind='ISSUE'"
                            ),
                            {"id": row["id"]},
                        )
                    ).scalar_one()
                )

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "server": ("test", 80),
        "client": ("127.0.0.1", 12345),
        "headers": [
            (b"content-type", b"application/json"),
            (b"origin", settings().web_origin.encode()),
            (b"idempotency-key", uuid4().hex.encode()),
            (b"cookie", ("forge_session=" + c.cookies["forge_session"]).encode()),
        ],
    }

    def slow_commit(session):
        await_only(asyncio.sleep(0.05))

    event.listen(Session, "before_commit", slow_commit)
    try:
        await asyncio.wait_for(app(scope, receive_message, send), 5)
    finally:
        event.remove(Session, "before_commit", slow_commit)
    assert observations == [1], "Shipment success reached the client before ISSUE commit"
