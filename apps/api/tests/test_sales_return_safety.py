import asyncio
import json
from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_sales_orders import balance, detail
from test_sales_orders import sale as sale
from test_sales_returns import edit_return, issued, return_draft, reverse
from test_sales_shipments import post_shipment, reconcile_sale, stock_document

from forge_erp.core.config import settings
from forge_erp.core.db import sessions, set_tenant
from forge_erp.main import app
from forge_erp.workers.outbox import drain_outbox


@pytest.mark.parametrize(
    "marker",
    [
        "INSERT INTO forge.inventory_movements",
        "UPDATE forge.inventory_balances",
        "UPDATE forge.inventory_documents",
        "INSERT INTO forge.audit_events",
        "INSERT INTO forge.outbox_events",
        "INSERT INTO forge.idempotency_keys",
        "COMMIT",
    ],
)
async def test_return_post_failures_roll_back_all_facts_and_retry_original_key(
    catalog_client, sale, identities, monkeypatch, marker
):
    c = catalog_client
    order, original = await issued(c, sale)
    row, _ = await return_draft(c, original, "1")
    before = await balance(c, sale)
    execute = AsyncSession.execute
    key = uuid4().hex
    injected = False

    async def broken(self, statement, *args, **kwargs):
        nonlocal injected
        result = await execute(self, statement, *args, **kwargs)
        if marker in str(statement) and not injected:
            injected = True
            raise RuntimeError("injected return failure with confidential cost")
        return result

    def failed_commit(session):
        nonlocal injected
        injected = True
        raise RuntimeError("injected return commit failure")

    if marker == "COMMIT":
        event.listen(Session, "before_commit", failed_commit)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "execute", broken)
            failed = await post_shipment(c, row, key)
    finally:
        if marker == "COMMIT":
            event.remove(Session, "before_commit", failed_commit)
    assert injected and failed.status_code == 500, failed.text
    assert "injected" not in failed.text and "confidential" not in failed.text
    assert failed.headers["content-type"].startswith("application/problem+json")
    assert await balance(c, sale) == before
    assert (await stock_document(c, row))["status"] == "DRAFT"
    assert D((await detail(c, order))["lines"][0]["returned_base_qty"]) == 0
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        for table, where in [
            ("inventory_movements", "document_id=:id"),
            ("audit_events", "resource_id=:id AND action='sales.document.post'"),
            ("outbox_events", "payload->>'resource_id'=:id AND event_type='sales.document.post'"),
        ]:
            assert (
                await db.execute(
                    text(f"SELECT count(*) FROM forge.{table} WHERE {where}"), {"id": row["id"]}
                )
            ).scalar_one() == 0
    assert (await post_shipment(c, row, key)).status_code == 200
    assert D((await detail(c, order))["lines"][0]["returned_base_qty"]) == 1
    await reconcile_sale(sale, identities[0])


@pytest.mark.parametrize("kind", ["shipment", "return"])
async def test_reversal_commit_failure_preserves_original_facts_and_is_retryable(
    catalog_client, sale, identities, kind
):
    c = catalog_client
    _, original = await issued(c, sale)
    target = original
    if kind == "return":
        row, _ = await return_draft(c, original)
        posted = await post_shipment(c, row)
        assert posted.status_code == 200, posted.text
        target = posted.json()
    before = await balance(c, sale)
    key = uuid4().hex

    def failed_commit(session):
        raise RuntimeError("injected reversal commit failure")

    event.listen(Session, "before_commit", failed_commit)
    try:
        failed = await reverse(c, target, key)
    finally:
        event.remove(Session, "before_commit", failed_commit)
    assert failed.status_code == 500, failed.text
    assert await balance(c, sale) == before
    assert (await stock_document(c, target))["status"] == "POSTED"
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        assert (
            await db.execute(text("SELECT count(*) FROM forge.inventory_reversals"))
        ).scalar_one() == 0
    assert (await reverse(c, target, key)).status_code == 200
    await reconcile_sale(sale, identities[0])


@pytest.mark.parametrize("removed", ["cost", "price", "both"])
async def test_return_quantity_staff_can_post_and_reverse_without_financial_read_leaks(
    catalog_client, sale, identities, removed
):
    c = catalog_client
    _, original = await issued(c, sale)
    row, body = await return_draft(c, original)
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
    extra = await create(c, "sales/returns", body)
    assert extra.status_code == 201, extra.text
    updated = await edit_return(c, row, body)
    assert updated.status_code == 200, updated.text
    posted = await post_shipment(c, updated.json())
    assert posted.status_code == 200, posted.text
    assert set(posted.json()) == {"id", "status", "version", "request_id"}
    for path in ["orders", "documents", "documents/" + row["id"]]:
        response = await c.get("/api/v1/sales/" + path)
        assert response.status_code == 200, response.text
        for field in [
            "actual_cost",
            "return_cost",
            "inventory_value_delta",
            "gross_margin",
            "net_cost",
        ]:
            assert field not in response.text
        if removed != "cost":
            for field in ["unit_price", '"amount"', "net_sales", "sales_amount", "return_amount"]:
                assert field not in response.text
    undone = await reverse(c, posted.json())
    assert undone.status_code == 200, undone.text
    assert set(undone.json()) == {"id", "status", "version", "request_id"}


async def test_sales_return_permission_does_not_grant_other_actions(
    catalog_client, sale, identities
):
    c = catalog_client
    _, original = await issued(c, sale)
    _, body = await return_draft(c, original)
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                "AND permission_code NOT IN ('sales.return','profile.read')"
            ),
            identities[0],
        )
    created = await create(c, "sales/returns", body)
    assert created.status_code == 201, created.text
    posted = await post_shipment(c, created.json())
    assert posted.status_code == 200, posted.text
    assert (await reverse(c, posted.json())).status_code == 403
    assert (await reverse(c, original)).status_code == 403
    for path in ["orders", "documents", "documents/" + created.json()["id"], "price-history"]:
        assert (await c.get("/api/v1/sales/" + path)).status_code == 403


@pytest.mark.parametrize("removed", ["sales.return", "sales.reverse"])
async def test_revoked_return_or_reverse_permissions_block_cached_receipts(
    catalog_client, sale, identities, removed
):
    c = catalog_client
    _, original = await issued(c, sale)
    _, body = await return_draft(c, original)
    create_key, update_key, post_key, reverse_key = [uuid4().hex for _ in range(4)]
    created = await create(c, "sales/returns", body, create_key)
    assert created.status_code == 201, created.text
    row = created.json()
    updated = await edit_return(c, row, body, update_key)
    assert updated.status_code == 200, updated.text
    posted = await post_shipment(c, updated.json(), post_key)
    assert posted.status_code == 200, posted.text
    undone = await reverse(c, posted.json(), reverse_key)
    assert undone.status_code == 200, undone.text
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                "AND permission_code=:removed"
            ),
            identities[0] | {"removed": removed},
        )
    assert (await reverse(c, posted.json(), reverse_key)).status_code == 403
    if removed == "sales.return":
        assert (await create(c, "sales/returns", body, create_key)).status_code == 403
        assert (await edit_return(c, row, body, update_key)).status_code == 403
        assert (await post_shipment(c, updated.json(), post_key)).status_code == 403


async def test_expired_return_and_reverse_idempotency_records_cannot_repeat_facts(
    catalog_client, sale, identities
):
    c = catalog_client
    _, original = await issued(c, sale)
    row, _ = await return_draft(c, original)
    key = uuid4().hex
    posted = await post_shipment(c, row, key)
    assert posted.status_code == 200, posted.text
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        await db.execute(text("DELETE FROM forge.idempotency_keys"))
    assert (await post_shipment(c, row, key)).status_code == 409
    reverse_key = uuid4().hex
    undone = await reverse(c, posted.json(), reverse_key)
    assert undone.status_code == 200, undone.text
    before = await balance(c, sale)
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        await db.execute(text("DELETE FROM forge.idempotency_keys"))
    assert (await reverse(c, posted.json(), reverse_key)).status_code == 409
    assert await balance(c, sale) == before


async def test_return_rejects_forged_financial_and_inventory_fields(
    catalog_client, sale, identities
):
    c = catalog_client
    order, original = await issued(c, sale)
    row, body = await return_draft(c, original)
    for field, value in [
        ("unit_price", "1"),
        ("amount", "1"),
        ("actual_cost", "1"),
        ("return_cost", "1"),
        ("unit_to_base_factor", "1"),
        ("reservation_id", str(uuid4())),
        ("warehouse_id", sale["warehouse_id"]),
        ("organization_id", str(identities[1]["org"])),
    ]:
        bad = await create(
            c, "sales/returns", body | {"lines": [body["lines"][0] | {field: value}]}
        )
        assert bad.status_code == 422, (field, bad.text)
    assert (
        await create(c, "sales/returns", body | {"organization_id": str(identities[1]["org"])})
    ).status_code == 422
    assert (
        await create(c, "sales/returns", body | {"lines": body["lines"] * 2})
    ).status_code == 422
    for source in [order, row]:
        bad = await create(c, "sales/returns", body | {"source_id": source["id"]})
        assert bad.status_code in {404, 409}, bad.text
    bad = await edit_return(c, row, body | {"source_id": order["id"]})
    assert bad.status_code == 409 and bad.json()["code"] == "INVALID_SOURCE"
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


async def test_posted_return_is_immutable_and_invisible_across_tenants(
    catalog_client, sale, identities
):
    c = catalog_client
    _, original = await issued(c, sale)
    row, body = await return_draft(c, original)
    posted = await post_shipment(c, row)
    assert posted.status_code == 200, posted.text
    for sql in [
        "UPDATE forge.sales_document_lines SET amount=0 WHERE document_id=:id",
        "UPDATE forge.sales_document_lines SET return_cost=0 WHERE document_id=:id",
        "DELETE FROM forge.sales_document_lines WHERE document_id=:id",
        "UPDATE forge.inventory_document_lines SET qty=2,base_qty=2 WHERE document_id=:id",
        "UPDATE forge.inventory_documents SET reason='changed' WHERE id=:id",
    ]:
        with pytest.raises(DBAPIError):
            async with sessions.begin() as db:
                await set_tenant(db, identities[0]["org"])
                await db.execute(text(sql), {"id": row["id"]})
    for organization in [identities[1]["org"], None]:
        async with sessions.begin() as db:
            if organization:
                await set_tenant(db, organization)
            for table in ["sales_documents", "sales_document_lines", "inventory_movements"]:
                assert (
                    await db.execute(text(f"SELECT count(*) FROM forge.{table}"))
                ).scalar_one() == 0
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
    assert (await reverse(c, posted.json())).status_code == 404
    assert (await create(c, "sales/returns", body)).status_code == 404
    assert (await c.get("/api/v1/sales/documents")).json()["items"] == []


async def test_return_audit_outbox_and_commit_boundary_do_not_depend_on_redis(
    catalog_client, sale, identities, monkeypatch
):
    from redis.asyncio import Redis

    c = catalog_client
    _, original = await issued(c, sale)
    row, _ = await return_draft(c, original)
    delivered = False
    observations = []
    request_id = str(uuid4())
    path = "/api/v1/sales/documents/" + row["id"] + "/post"

    async def receive_message():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {
                "type": "http.request",
                "body": json.dumps({"expected_version": row["version"]}).encode(),
                "more_body": False,
            }
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.start":
            assert message["status"] == 200
            assert dict(message["headers"])[b"x-request-id"].decode() == request_id
            async with sessions.begin() as db:
                await set_tenant(db, identities[0]["org"])
                observations.append(
                    (
                        await db.execute(
                            text(
                                "SELECT count(*) FROM forge.inventory_movements "
                                "WHERE document_id=:id AND kind='RECEIVE'"
                            ),
                            {"id": row["id"]},
                        )
                    ).scalar_one()
                )

    async def unavailable(*args, **kwargs):
        raise ConnectionError("Redis unavailable")

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
            (b"x-request-id", request_id.encode()),
            (b"origin", settings().web_origin.encode()),
            (b"idempotency-key", uuid4().hex.encode()),
            (b"cookie", ("forge_session=" + c.cookies["forge_session"]).encode()),
        ],
    }
    with monkeypatch.context() as patch:
        patch.setattr(Redis, "execute_command", unavailable)
        await asyncio.wait_for(app(scope, receive_message, send), 5)
    assert observations == [1], "Return success headers arrived before committed inventory facts"
    before = await balance(c, sale)
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        audit = (
            await db.execute(
                text(
                    "SELECT before,after,actor_id,request_id FROM forge.audit_events "
                    "WHERE action='sales.document.post' AND resource_id=:id"
                ),
                {"id": row["id"]},
            )
        ).one()
        assert audit.actor_id == identities[0]["user"]
        assert audit.request_id == request_id
        assert audit.before["document"]["status"] == "DRAFT"
        assert audit.after["document"]["status"] == "POSTED"
        assert D(audit.after["lines"][0]["actual_cost"]) == 11
        payload = (
            await db.execute(
                text(
                    "SELECT payload FROM forge.outbox_events "
                    "WHERE event_type='sales.document.post' "
                    "AND payload->>'resource_id'=:id"
                ),
                {"id": row["id"]},
            )
        ).scalar_one()
        assert set(payload) <= {"resource_id", "resource_type", "version"}
    await drain_outbox(identities[0]["org"])
    await drain_outbox(identities[0]["org"])
    assert await balance(c, sale) == before

    reverse_request_id = str(uuid4())
    posted = await stock_document(c, row)
    undone = await c.post(
        "/api/v1/sales/documents/" + row["id"] + "/reverse",
        headers={"Idempotency-Key": uuid4().hex, "X-Request-ID": reverse_request_id},
        json={"expected_version": posted["version"], "reason": "验证冲销审计归属"},
    )
    assert undone.status_code == 200, undone.text
    assert undone.headers["X-Request-ID"] == reverse_request_id
    assert undone.json()["request_id"] == reverse_request_id
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        reverse_audit = (
            await db.execute(
                text(
                    "SELECT actor_id,request_id FROM forge.audit_events "
                    "WHERE action='sales.document.reverse' AND resource_id=:id"
                ),
                {"id": row["id"]},
            )
        ).one()
        assert reverse_audit.actor_id == identities[0]["user"]
        assert reverse_audit.request_id == reverse_request_id


async def test_multiline_return_failure_rolls_back_prior_received_line(
    catalog_client, sale, identities, monkeypatch
):
    from test_sales_orders import action, opening, so

    from forge_erp.modules.inventory.application.engine import InventoryEngine

    c = catalog_client
    product = (await c.get("/api/v1/products/" + sale["lines"][0]["product_id"])).json()
    second = (
        await create(
            c,
            "products",
            {
                "sku": "RETURN-SECOND",
                "name": "退货第二行",
                "category_id": product["category_id"],
                "attributes": product["attributes"],
                "base_unit_id": sale["lines"][0]["unit_id"],
            },
        )
    ).json()
    sale = sale | {"lines": sale["lines"] + [sale["lines"][0] | {"product_id": second["id"]}]}
    await opening(c, sale)
    order = await so(c, sale)
    assert (await action(c, order)).status_code == 200
    order_lines = (await detail(c, order))["lines"]
    shipment = await create(
        c,
        "sales/shipments",
        {
            "source_id": order["id"],
            "reason": "两行销售",
            "lines": [{"source_line_id": line["id"], "qty": "3"} for line in order_lines],
        },
    )
    assert shipment.status_code == 201, shipment.text
    original = shipment.json()
    assert (await post_shipment(c, original)).status_code == 200
    source_lines = (await stock_document(c, original))["lines"]
    drafted = await create(
        c,
        "sales/returns",
        {
            "source_id": original["id"],
            "reason": "两行原子退货",
            "lines": [{"source_line_id": line["id"], "qty": "1"} for line in source_lines],
        },
    )
    assert drafted.status_code == 201, drafted.text
    row = drafted.json()
    before = await balance(c, sale)
    change = InventoryEngine.change
    count = 0

    async def broken(self, *args, **kwargs):
        nonlocal count
        result = await change(self, *args, **kwargs)
        count += 1
        if count == 2:
            raise RuntimeError("second return line failed")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(InventoryEngine, "change", broken)
        failed = await post_shipment(c, row)
    assert count == 2 and failed.status_code == 500, failed.text
    assert await balance(c, sale) == before
    assert all(D(line["returned_base_qty"]) == 0 for line in (await detail(c, order))["lines"])
    assert (await post_shipment(c, row)).status_code == 200
    await reconcile_sale(sale, identities[0])
