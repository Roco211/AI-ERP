import json
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from forge_erp.core import db as db_module
from forge_erp.core.config import Settings, settings
from forge_erp.core.db import sessions, set_tenant
from forge_erp.core.errors import Problem
from forge_erp.core.rate_limit import check_login_rate
from forge_erp.core.security import fingerprint


@given(st.dictionaries(st.text(max_size=20), st.integers(), max_size=10))
def test_request_fingerprint_ignores_object_key_order(value):
    assert fingerprint(value) == fingerprint(dict(reversed(list(value.items()))))


def test_production_rejects_insecure_cookie():
    with pytest.raises(ValueError, match="HTTPS and Secure"):
        Settings(app_env="production", cookie_secure=False, web_origin="https://erp.example")


async def test_all_tenant_tables_force_rls_and_both_policies():
    async with sessions.begin() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,"
                    "p.polqual,p.polwithcheck "
                    "FROM pg_class c JOIN pg_policy p ON p.polrelid=c.oid "
                    "WHERE c.relnamespace='forge'::regnamespace"
                )
            )
        ).all()
        assert len(rows) == 9
        assert all(
            row.relrowsecurity and row.relforcerowsecurity and row.polqual and row.polwithcheck
            for row in rows
        )


async def test_application_refuses_elevated_database_role(monkeypatch):
    elevated = create_async_engine(settings().migration_database_url)
    monkeypatch.setattr(db_module, "engine", elevated)
    try:
        with pytest.raises(RuntimeError, match="restricted forge_app"):
            await db_module.verify_database_role()
    finally:
        await elevated.dispose()


async def test_rls_context_rolls_back_and_uuidv7(identities):
    assert identities[0]["org"].version == 7
    with pytest.raises(RuntimeError):
        async with sessions.begin() as db:
            await set_tenant(db, identities[0]["org"])
            raise RuntimeError("abort")
    async with sessions.begin() as db:
        assert (await db.execute(text("SELECT count(*) FROM forge.users"))).scalar_one() == 0


async def test_actual_redis_rate_limit():
    key = uuid4().hex
    for _ in range(30):
        await check_login_rate(key)
    with pytest.raises(Problem) as error:
        await check_login_rate(key)
    assert error.value.code == "RATE_LIMITED"


async def test_login_missing_key_and_secure_cookie(client, identities, monkeypatch):
    body = {
        "organization_code": identities[0]["code"],
        "email": "same@example.test",
        "password": "test-only-password-8472",
    }
    assert (await client.post("/api/v1/auth/login", json=body)).status_code == 422
    monkeypatch.setattr(settings(), "cookie_secure", True)
    response = await client.post(
        "/api/v1/auth/login", json=body, headers={"Idempotency-Key": uuid4().hex}
    )
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


async def test_request_id_sanitized_and_unknown_endpoint_problem(client):
    response = await client.get("/not-a-route", headers={"X-Request-ID": "not a valid id"})
    assert response.status_code == 404
    assert response.headers["x-request-id"] != "not a valid id"
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_contract_has_no_tenant_login_override():
    from forge_erp.main import app

    schema = app.openapi()
    inputs = schema["components"]["schemas"]["LoginInput"]
    assert "organization_id" not in inputs["properties"]
    assert inputs["additionalProperties"] is False
    assert "/api/v1/auth/me" in schema["paths"]
    assert "password_hash" not in json.dumps(schema)
    for status in ("401", "403", "409", "422", "429", "500", "503"):
        media = schema["paths"]["/api/v1/auth/login"]["post"]["responses"][status]["content"]
        # Regression: an empty problem+json schema collapses generated client errors to {}.
        assert media["application/problem+json"]["schema"]["$ref"].endswith("/ProblemDetails")
