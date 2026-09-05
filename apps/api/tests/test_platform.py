import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from forge_erp.core.config import settings
from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import sessions, set_tenant, verify_database_role
from forge_erp.core.errors import Problem
from forge_erp.core.idempotency import execute_once
from forge_erp.core.security import token_hash
from forge_erp.modules.audit.service import record_event
from forge_erp.workers.outbox import drain_outbox

PASSWORD = "test-only-password-8472"


def credentials(identity):
    return {
        "organization_code": identity["code"],
        "email": "same@example.test",
        "password": PASSWORD,
    }


async def login(client, identity, key=None):
    return await client.post(
        "/api/v1/auth/login",
        json=credentials(identity),
        headers={"Idempotency-Key": key or uuid4().hex},
    )


async def test_health_ready_version_request_id(client):
    for path in ("/healthz", "/readyz", "/api/v1/system/version"):
        response = await client.get(path, headers={"X-Request-ID": "bootstrap-check"})
        assert response.status_code == 200
        assert response.headers["x-request-id"] == "bootstrap-check"
    metadata = (await client.get("/api/v1/system/version")).json()
    assert metadata["version"] == "0.11.0"
    assert metadata["milestone"] == "AI Assistant"


async def test_login_me_logout_and_replay(client, identities):
    key = uuid4().hex
    response = await login(client, identities[0], key)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    token = client.cookies.get("forge_session")
    replay = await login(client, identities[0], key)
    assert replay.status_code == 200 and client.cookies.get("forge_session") == token
    profile = (await client.get("/api/v1/auth/me")).json()
    assert profile["organization_id"] == str(identities[0]["org"])
    async with sessions.begin() as db:
        await set_tenant(db, identities[0]["org"])
        hashes = (await db.execute(text("SELECT token_hash FROM forge.sessions"))).scalars().all()
        assert hashes == [token_hash(token)] and token not in hashes
        assert (await db.execute(text("SELECT count(*) FROM forge.audit_events"))).scalar() == 1
        assert (await db.execute(text("SELECT count(*) FROM forge.outbox_events"))).scalar() == 1
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    replay = await login(client, identities[0], key)
    assert replay.status_code == 409 and replay.json()["code"] == "LOGIN_REPLAY_EXPIRED"


async def test_credentials_and_error_redaction(client, identities):
    body = credentials(identities[0]) | {"password": "very-secret-invalid"}
    response = await client.post(
        "/api/v1/auth/login", json=body, headers={"Idempotency-Key": uuid4().hex}
    )
    assert response.status_code == 401
    assert "very-secret-invalid" not in response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["request_id"] == response.headers["x-request-id"]
    response = await client.post(
        "/api/v1/auth/login",
        json=body | {"organization_id": "fake"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 422 and "very-secret-invalid" not in response.text


async def test_csrf_required_even_on_login(client, identities):
    response = await client.post(
        "/api/v1/auth/login",
        json=credentials(identities[0]),
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403 and response.json()["code"] == "CSRF_REJECTED"
    client.headers.pop("Origin")
    assert (await client.post("/api/v1/auth/logout")).status_code == 403


async def test_real_role_and_missing_context():
    await verify_database_role()
    async with sessions.begin() as db:
        assert (await db.execute(text("SELECT count(*) FROM forge.users"))).scalar() == 0
        assert (await db.execute(text("SELECT count(*) FROM forge.organizations"))).scalar() == 0


async def test_rls_read_write_force_and_pool_reset(identities):
    a, b = identities
    async with sessions.begin() as db:
        await set_tenant(db, a["org"])
        rows = (await db.execute(text("SELECT id FROM forge.users"))).scalars().all()
        assert rows == [a["user"]]
        assert not (
            await db.execute(text("SELECT id FROM forge.users WHERE id=:id"), {"id": b["user"]})
        ).all()
        protected = (
            await db.execute(
                text(
                    "SELECT relrowsecurity,relforcerowsecurity "
                    "FROM pg_class WHERE relnamespace='forge'::regnamespace AND relname='users'"
                )
            )
        ).one()
        assert protected == (True, True)
    async with sessions.begin() as db:
        assert (await db.execute(text("SELECT count(*) FROM forge.users"))).scalar() == 0
    with pytest.raises(DBAPIError):
        async with sessions.begin() as db:
            await set_tenant(db, a["org"])
            await db.execute(
                text(
                    "INSERT INTO forge.outbox_events "
                    "(organization_id,event_type,payload,request_id) "
                    "VALUES (:org,'test','{}','test')"
                ),
                {"org": b["org"]},
            )


async def test_tenant_cannot_update_or_delete_other_events(identities):
    a, b = identities
    async with sessions.begin() as db:
        await set_tenant(db, b["org"])
        await record_event(
            db,
            RuntimeContext(b["org"], b["user"], frozenset(), "test"),
            "identity.session.created",
            uuid4(),
        )
    async with sessions.begin() as db:
        await set_tenant(db, a["org"])
        rows = await db.execute(
            text(
                "UPDATE forge.outbox_events SET processed_at=now() "
                "WHERE organization_id=:org RETURNING id"
            ),
            b,
        )
        assert rows.all() == []
    with pytest.raises(DBAPIError):
        async with sessions.begin() as db:
            await set_tenant(db, b["org"])
            await db.execute(text("DELETE FROM forge.audit_events"))


async def test_same_email_distinct_tenants_and_permission_enforcement(client, identities):
    a, b = identities
    await login(client, a)
    # Client-supplied tenant headers do not change authenticated context.
    me = await client.get("/api/v1/auth/me", headers={"X-Organization-ID": str(b["org"])})
    assert me.json()["user_id"] == str(a["user"])
    await login(client, b)
    assert (await client.get("/api/v1/auth/me")).json()["user_id"] == str(b["user"])
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(text("DELETE FROM forge.role_permissions WHERE organization_id=:org"), b)
    assert (await client.get("/api/v1/auth/me")).status_code == 403


async def test_revoked_disabled_expired_session(client, identities):
    a = identities[0]
    await login(client, a)
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "UPDATE forge.sessions SET expires_at=now()-interval '1 second' "
                "WHERE organization_id=:org"
            ),
            a,
        )
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    await login(client, a)
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(text("UPDATE forge.users SET active=false WHERE organization_id=:org"), a)
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_atomic_rollback_and_idempotency_concurrency(identities):
    a = identities[0]
    ctx = RuntimeContext(a["org"], a["user"], frozenset(), "atomic-test")

    async def run(value=1, fail=False):
        async with sessions.begin() as db:
            await set_tenant(db, a["org"])

            async def mutate():
                await record_event(db, ctx, "identity.session.created", uuid4())
                if fail:
                    raise RuntimeError("rollback")
                return {"value": value}

            return await execute_once(db, ctx, "test", "concurrent-key", {"value": value}, mutate)

    with pytest.raises(RuntimeError):
        await run(fail=True)
    async with sessions.begin() as db:
        await set_tenant(db, a["org"])
        for table in ("audit_events", "outbox_events", "idempotency_keys"):
            assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar() == 0
    assert await asyncio.gather(*(run() for _ in range(5))) == [{"value": 1}] * 5
    with pytest.raises(Problem, match="different request"):
        await run(value=2)
    async with sessions.begin() as db:
        await set_tenant(db, a["org"])
        assert (await db.execute(text("SELECT count(*) FROM forge.audit_events"))).scalar() == 1


async def test_outbox_poll_is_repeatable(identities):
    a = identities[0]
    async with sessions.begin() as db:
        await set_tenant(db, a["org"])
        await record_event(
            db,
            RuntimeContext(a["org"], a["user"], frozenset(), "outbox-test"),
            "identity.session.created",
            uuid4(),
        )
    assert await drain_outbox(a["org"]) == 1
    async with sessions.begin() as db:
        await set_tenant(db, a["org"])
        row = (
            await db.execute(text("SELECT processed_at,attempts FROM forge.outbox_events"))
        ).one()
        assert row.processed_at and row.attempts == 1
    assert await drain_outbox(a["org"]) == 0
    async with sessions.begin() as db:
        await set_tenant(db, a["org"])
        assert (await db.execute(text("SELECT attempts FROM forge.outbox_events"))).scalar() == 1
