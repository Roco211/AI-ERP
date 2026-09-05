"""Observe the actual ASGI response boundary, unlike buffered HTTPX responses."""

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from sqlalchemy.util.concurrency import await_only
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_purchasing import action, po, receive
from test_purchasing import purchase as purchase
from test_sales_orders import opening, so

from forge_erp.core.config import settings
from forge_erp.core.db import sessions, set_tenant
from forge_erp.main import app


@pytest.mark.parametrize("operation", ["catalog", "inventory", "purchasing", "sales"])
async def test_success_headers_require_committed_data(
    catalog_client, purchase, identities, operation
):
    c = catalog_client
    code = "COMMIT-" + uuid4().hex
    if operation == "catalog":
        path = "/api/v1/categories"
        body = {"code": code, "name": "Committed before response"}
        query = "SELECT count(*) FROM forge.categories WHERE code=:code"
        params = {"code": code}
    elif operation == "inventory":
        path = "/api/v1/inventory/openings"
        body = {
            "warehouse_id": purchase["warehouse_id"],
            "reason": code,
            "lines": [
                {k: v for k, v in purchase["lines"][0].items() if k != "unit_price"}
                | {"input_unit_cost": "1"}
            ],
        }
        query = "SELECT count(*) FROM forge.inventory_documents WHERE reason=:code"
        params = {"code": code}
    elif operation == "sales":
        customer = (await create(c, "customers", {"code": "COMMIT-C", "name": "Sales"})).json()
        sales_body = {
            "customer_id": customer["id"],
            "warehouse_id": purchase["warehouse_id"],
            "reason": code,
            "lines": [purchase["lines"][0] | {"pricing_mode": "MANUAL", "qty": "7"}],
        }
        await opening(c, sales_body)
        row = await so(c, sales_body)
        path = "/api/v1/sales/orders/" + row["id"] + "/confirm"
        body = {"expected_version": row["version"]}
        query = "SELECT count(*) FROM forge.sales_orders WHERE id=:id AND status='CONFIRMED'"
        params = {"id": row["id"]}
    else:
        order = await po(c, purchase)
        await action(c, order)
        row, _ = await receive(c, order)
        path = "/api/v1/purchasing/documents/" + row["id"] + "/post"
        body = {"expected_version": row["version"]}
        query = "SELECT count(*) FROM forge.inventory_documents WHERE id=:id AND status='POSTED'"
        params = {"id": row["id"]}
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
            assert message["status"] in (200, 201)
            async with sessions.begin() as db:
                await set_tenant(db, identities[0]["org"])
                observations.append((await db.execute(text(query), params)).scalar_one())

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
    assert observations == [1], "Success reached the client before the database commit"


async def test_commit_failure_returns_problem_instead_of_false_success(catalog_client, identities):
    code = "ROLLBACK-" + uuid4().hex

    def fail_commit(session):
        raise RuntimeError("injected commit failure")

    event.listen(Session, "before_commit", fail_commit)
    try:
        response = await catalog_client.post(
            "/api/v1/categories",
            json={"code": code, "name": "rollback"},
            headers={"Idempotency-Key": uuid4().hex},
        )
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "injected" not in response.text
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.categories WHERE code=:code"), {"code": code}
            )
        ).scalar_one() == 0
