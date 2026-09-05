"""Shipment receipt replay never repeats facts or replaces current authorization."""

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_sales_orders import action, opening, so
from test_sales_orders import sale as sale
from test_sales_returns import reverse
from test_sales_shipments import post_shipment, reconcile_sale, shipment, stock_document

from forge_erp.core.config import settings
from forge_erp.core.db import sessions, set_tenant


@pytest.fixture
async def shipment_draft(catalog_client, sale):
    await opening(catalog_client, sale)
    order = await so(catalog_client, sale)
    confirmed = await action(catalog_client, order)
    assert confirmed.status_code == 200, confirmed.text
    draft, _ = await shipment(catalog_client, order)
    return draft


@pytest.fixture
async def shipment_posted(catalog_client, shipment_draft):
    key = uuid4().hex
    response = await post_shipment(catalog_client, shipment_draft, key)
    assert response.status_code == 200, response.text
    return key, response.json()


async def facts(identity):
    """Read complete business rows as Decimal values through the RLS application role."""
    result = {}
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        for table in (
            "inventory_documents",
            "inventory_document_lines",
            "inventory_balances",
            "inventory_reservations",
            "inventory_reversals",
            "inventory_movements",
            "sales_orders",
            "sales_order_lines",
            "sales_documents",
            "sales_document_lines",
            "audit_events",
            "outbox_events",
        ):
            scope = ""
            if table == "audit_events":
                scope = " AND action LIKE 'sales.%'"
            elif table == "outbox_events":
                scope = " AND event_type LIKE 'sales.%'"
            result[table] = (
                (
                    await db.execute(
                        text(
                            f"SELECT * FROM forge.{table} WHERE organization_id=:org"
                            f"{scope} ORDER BY id"
                        ),
                        identity,
                    )
                )
                .mappings()
                .all()
            )
    return result


async def receipts(identity, draft):
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        return (
            (
                await db.execute(
                    text(
                        "SELECT *,expires_at>now() AS live FROM forge.idempotency_keys "
                        "WHERE organization_id=:org AND operation=:operation "
                        "ORDER BY actor_id,key"
                    ),
                    identity | {"operation": "sales.document.post:" + draft["id"]},
                )
            )
            .mappings()
            .all()
        )


async def expire_receipt(identity, draft, key, mode):
    """Exercise inline TTL expiry and the equivalent of prior committed cleanup.

    There is no dedicated idempotency cleanup worker currently. The cleaned case
    deliberately removes only this expired receipt, using forge_app and tenant RLS.
    """
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        where = "organization_id=:org AND actor_id=:user AND operation=:operation AND key=:key"
        params = identity | {"operation": "sales.document.post:" + draft["id"], "key": key}
        expired = await db.execute(
            text(
                "UPDATE forge.idempotency_keys SET expires_at=now()-interval '1 second' "
                f"WHERE {where} RETURNING key"
            ),
            params,
        )
        assert expired.scalar_one() == key
        if mode == "cleaned":
            deleted = await db.execute(
                text(
                    f"DELETE FROM forge.idempotency_keys WHERE {where} "
                    "AND expires_at<=now() RETURNING key"
                ),
                params,
            )
            assert deleted.scalar_one() == key


def remove_permission(identity, permission):
    admin = create_engine(settings().migration_database_url)
    try:
        with admin.begin() as db:
            db.execute(
                text(
                    "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                    "AND role_id=:role AND permission_code=:permission"
                ),
                identity | {"permission": permission},
            )
    finally:
        admin.dispose()


async def login(c, identity, email="same@example.test"):
    response = await create(
        c,
        "auth/login",
        {
            "organization_code": identity["code"],
            "email": email,
            "password": "test-only-password-8472",
        },
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("same_key", [False, True], ids=["different-keys", "same-key"])
async def test_concurrent_post_of_one_shipment_has_exactly_one_effect(
    catalog_client, shipment_draft, sale, identities, same_key
):
    keys = [uuid4().hex, uuid4().hex]
    if same_key:
        keys[1] = keys[0]
    responses = await asyncio.wait_for(
        asyncio.gather(*(post_shipment(catalog_client, shipment_draft, key) for key in keys)),
        timeout=10,
    )
    assert sorted(r.status_code for r in responses) == ([200, 200] if same_key else [200, 409])
    success = next(r for r in responses if r.status_code == 200)
    if same_key:
        assert responses[0].json() == responses[1].json()
    else:
        failure = next(r for r in responses if r.status_code == 409)
        assert failure.json()["code"] == "INVALID_DOCUMENT_STATE"
    state = await facts(identities[0])
    movements = [r for r in state["inventory_movements"] if r["kind"] == "ISSUE"]
    assert len(movements) == 1 and movements[0]["base_qty"] == -3
    assert movements[0]["value_delta"] == -33
    reservation = state["inventory_reservations"][0]
    assert (reservation["remaining_qty"], reservation["consumed_qty"]) == (4, 3)
    balance = state["inventory_balances"][0]
    assert (balance["on_hand_qty"], balance["reserved_qty"], balance["inventory_value"]) == (
        7,
        4,
        77,
    )
    assert sum(r["action"] == "sales.document.post" for r in state["audit_events"]) == 1
    assert sum(r["event_type"] == "sales.document.post" for r in state["outbox_events"]) == 1
    cached = await receipts(identities[0], shipment_draft)
    assert len(cached) == 1 and cached[0]["response"] == success.json()
    assert cached[0]["key"] == keys[responses.index(success)]
    assert (await stock_document(catalog_client, shipment_draft))["version"] == 2
    await reconcile_sale(sale, identities[0])


@pytest.mark.parametrize("terminal", ["POSTED", "REVERSED"])
async def test_live_post_receipt_is_historical_and_changed_body_conflicts(
    catalog_client, shipment_draft, shipment_posted, identities, terminal
):
    key, posted = shipment_posted
    if terminal == "REVERSED":
        reversed_response = await reverse(catalog_client, posted)
        assert reversed_response.status_code == 200, reversed_response.text
    before = await facts(identities[0])
    cached = await receipts(identities[0], shipment_draft)
    replay = await post_shipment(catalog_client, shipment_draft, key)
    assert replay.status_code == 200 and replay.json() == posted
    assert replay.headers["x-request-id"] != replay.json()["request_id"]
    changed = await post_shipment(
        catalog_client, shipment_draft | {"version": posted["version"]}, key
    )
    assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert (await stock_document(catalog_client, shipment_draft))["status"] == terminal
    assert await facts(identities[0]) == before
    assert await receipts(identities[0], shipment_draft) == cached


@pytest.mark.parametrize("expiry", ["expired", "cleaned"])
@pytest.mark.parametrize("terminal", ["POSTED", "REVERSED"])
async def test_expired_post_receipt_cannot_repeat_terminal_shipment_even_concurrently(
    catalog_client, shipment_draft, shipment_posted, sale, identities, expiry, terminal
):
    key, posted = shipment_posted
    if terminal == "REVERSED":
        reversed_response = await reverse(catalog_client, posted)
        assert reversed_response.status_code == 200, reversed_response.text
    before = await facts(identities[0])
    await expire_receipt(identities[0], shipment_draft, key, expiry)
    after_expiry = await receipts(identities[0], shipment_draft)
    assert not any(row["live"] for row in after_expiry)
    # Both serialized reuse of the expired key and a competing fresh key must
    # reach the terminal-state guard, without minting a new receipt or stock fact.
    responses = await asyncio.wait_for(
        asyncio.gather(
            *(post_shipment(catalog_client, shipment_draft, k) for k in (key, key, uuid4().hex))
        ),
        timeout=10,
    )
    for response in responses:
        assert response.status_code == 409 and response.json()["code"] == "INVALID_DOCUMENT_STATE"
    changed = await post_shipment(
        catalog_client, shipment_draft | {"version": posted["version"]}, key
    )
    assert changed.status_code == 409 and changed.json()["code"] == "INVALID_DOCUMENT_STATE"
    assert await facts(identities[0]) == before
    # Inline deletion rolls back with a rejected command; do not claim it is a
    # committed cleanup. A previously cleaned row must remain absent instead.
    assert await receipts(identities[0], shipment_draft) == after_expiry
    await reconcile_sale(sale, identities[0])


@pytest.mark.parametrize("expiry", ["live", "expired", "cleaned"])
async def test_revoked_ship_permission_blocks_all_post_receipt_paths(
    catalog_client, shipment_draft, shipment_posted, identities, expiry
):
    key, _ = shipment_posted
    if expiry != "live":
        await expire_receipt(identities[0], shipment_draft, key, expiry)
    before = await facts(identities[0])
    cached = await receipts(identities[0], shipment_draft)
    remove_permission(identities[0], "sales.ship")
    response = await post_shipment(catalog_client, shipment_draft, key)
    assert response.status_code == 403 and response.json()["code"] == "PERMISSION_DENIED"
    assert await facts(identities[0]) == before
    assert await receipts(identities[0], shipment_draft) == cached


async def test_revoked_read_price_and_cost_permissions_still_allow_authorized_ship_receipt(
    catalog_client, shipment_draft, shipment_posted, identities
):
    key, posted = shipment_posted
    before = await facts(identities[0])
    for permission in ("sales.read", "product.price.read", "product.cost.read"):
        remove_permission(identities[0], permission)
    response = await post_shipment(catalog_client, shipment_draft, key)
    assert response.status_code == 200 and response.json() == posted
    assert set(response.json()) == {"id", "status", "version", "request_id"}
    detail = await catalog_client.get("/api/v1/sales/documents/" + shipment_draft["id"])
    assert detail.status_code == 403 and detail.json()["code"] == "PERMISSION_DENIED"
    assert await facts(identities[0]) == before


@pytest.mark.parametrize(
    "invalidation",
    ["no-cookie", "revoked-session", "expired-session", "inactive-user", "inactive-org"],
)
async def test_invalid_auth_context_cannot_replay_a_live_shipment_receipt(
    catalog_client, shipment_draft, shipment_posted, identities, invalidation
):
    c = catalog_client
    key, _ = shipment_posted
    before = await facts(identities[0])
    cached = await receipts(identities[0], shipment_draft)
    if invalidation == "no-cookie":
        c.cookies.clear()
    elif invalidation == "revoked-session":
        cookie = c.cookies.get("forge_session")
        assert cookie
        assert (await c.post("/api/v1/auth/logout")).status_code == 204
        # Reusing the actual old opaque token still cannot obtain a cached receipt.
        c.cookies.set("forge_session", cookie)
    else:
        statements = {
            "expired-session": "UPDATE forge.sessions SET expires_at=now()-interval '1 second' "
            "WHERE organization_id=:org AND user_id=:user",
            "inactive-user": "UPDATE forge.users SET active=false WHERE organization_id=:org "
            "AND id=:user",
            "inactive-org": "UPDATE forge.organizations SET active=false WHERE id=:org",
        }
        admin = create_engine(settings().migration_database_url)
        try:
            with admin.begin() as db:
                db.execute(text(statements[invalidation]), identities[0])
        finally:
            admin.dispose()
    response = await post_shipment(c, shipment_draft, key)
    assert response.status_code == 401 and response.json()["code"] == "UNAUTHENTICATED"
    assert await facts(identities[0]) == before
    assert await receipts(identities[0], shipment_draft) == cached


@pytest.mark.parametrize("identity_change", ["new-session", "other-actor", "other-tenant"])
async def test_receipt_scope_is_tenant_actor_not_session_or_client_key_alone(
    catalog_client, shipment_draft, shipment_posted, identities, password_hash, identity_change
):
    c = catalog_client
    key, posted = shipment_posted
    before = await facts(identities[0])
    cached = await receipts(identities[0], shipment_draft)
    assert (await c.post("/api/v1/auth/logout")).status_code == 204
    email = "same@example.test"
    identity = identities[0]
    if identity_change == "other-tenant":
        identity = identities[1]
    elif identity_change == "other-actor":
        email = "other-shipper@example.test"
        admin = create_engine(settings().migration_database_url)
        try:
            with admin.begin() as db:
                user = db.execute(
                    text(
                        "INSERT INTO forge.users(organization_id,email,display_name,password_hash) "
                        "VALUES(:org,:email,'Other shipper',:hash) RETURNING id"
                    ),
                    identity | {"email": email, "hash": password_hash},
                ).scalar_one()
                db.execute(
                    text("INSERT INTO forge.user_roles VALUES(:org,:user,:role)"),
                    identity | {"user": user},
                )
        finally:
            admin.dispose()
    await login(c, identity, email)
    response = await post_shipment(c, shipment_draft, key)
    if identity_change == "new-session":
        assert response.status_code == 200 and response.json() == posted
    elif identity_change == "other-actor":
        assert response.status_code == 409 and response.json()["code"] == "INVALID_DOCUMENT_STATE"
    else:
        assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"
        async with sessions.begin() as db:
            await set_tenant(db, identity["org"])
            assert (
                await db.execute(
                    text("SELECT count(*) FROM forge.inventory_documents WHERE id=:id"),
                    {"id": UUID(shipment_draft["id"])},
                )
            ).scalar_one() == 0
        assert await receipts(identity, shipment_draft) == []
    assert await facts(identities[0]) == before
    assert await receipts(identities[0], shipment_draft) == cached


async def test_same_key_is_independent_for_another_shipment_and_reverse_operation(
    catalog_client, shipment_draft, shipment_posted, sale, identities
):
    c = catalog_client
    key, first = shipment_posted
    source = await stock_document(c, shipment_draft)
    second, _ = await shipment(c, {"id": source["order_id"]})
    posted = await post_shipment(c, second, key)
    assert posted.status_code == 200, posted.text
    assert posted.json()["id"] == second["id"] != first["id"]
    state = await facts(identities[0])
    assert sum(row["kind"] == "ISSUE" for row in state["inventory_movements"]) == 2
    assert state["inventory_reservations"][0]["consumed_qty"] == 6
    assert state["inventory_balances"][0]["on_hand_qty"] == 4
    first_cache = await receipts(identities[0], shipment_draft)
    second_cache = await receipts(identities[0], second)
    assert len(first_cache) == len(second_cache) == 1
    assert first_cache[0]["key"] == second_cache[0]["key"] == key
    assert first_cache[0]["response"] == first
    assert second_cache[0]["response"] == posted.json()
    reversed_response = await reverse(c, posted.json(), key)
    assert reversed_response.status_code == 200, reversed_response.text
    assert reversed_response.json()["status"] == "REVERSED"
    assert (await stock_document(c, shipment_draft))["status"] == "POSTED"
    before = await facts(identities[0])
    replay = await post_shipment(c, second, key)
    assert replay.status_code == 200 and replay.json() == posted.json()
    assert await facts(identities[0]) == before
    await reconcile_sale(sale, identities[0])
