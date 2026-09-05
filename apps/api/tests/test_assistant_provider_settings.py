"""Web provider configuration: credentials stay secret and network calls stay bounded."""

import asyncio
import json
import socket
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import HumanMessage
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_platform import login

from forge_erp.core.config import settings
from forge_erp.core.db import sessions, set_tenant
from forge_erp.core.errors import Problem
from forge_erp.modules.assistant.application import providers
from forge_erp.modules.assistant.domain.providers import ProviderConnection, ProviderInput
from forge_erp.modules.assistant.infrastructure import credentials, network, provider

BASE = "/api/v1/ai/provider"
TEST_KEY = "test-only-provider-key-not-a-live-credential"


def provider_body(**changes):
    return {
        "name": "Test Provider",
        "base_url": "https://models.example.test/v1",
        "model": "test-model-v1",
        "enabled": True,
        "allow_private_network": False,
        "expected_version": 0,
        "api_key": TEST_KEY,
        **changes,
    }


def grant(identity, permissions):
    admin = create_engine(settings().migration_database_url)
    with admin.begin() as db:
        db.execute(text("DELETE FROM forge.role_permissions WHERE organization_id=:org"), identity)
        for permission in permissions | {"profile.read"}:
            db.execute(
                text("INSERT INTO forge.role_permissions VALUES(:org,:role,:permission)"),
                {**identity, "permission": permission},
            )
    admin.dispose()


@pytest.fixture
async def provider_client(client, identities):
    grant(identities[0], {"ai.provider.manage", "ai.use"})
    response = await login(client, identities[0])
    assert response.status_code == 200
    return client, identities[0]


async def save(client, *, key=None, **changes):
    return await client.put(
        BASE,
        json=provider_body(**changes),
        headers={"Idempotency-Key": key or uuid4().hex},
    )


async def connect(client, *, key=None, version=1):
    return await client.post(
        BASE + "/test",
        json={"expected_version": version},
        headers={"Idempotency-Key": key or uuid4().hex},
    )


async def test_provider_save_is_encrypted_no_echo_no_secret_audit_and_no_outbound(
    provider_client, monkeypatch
):
    client, identity = provider_client

    async def never_outbound(*args, **kwargs):
        raise AssertionError("Saving credentials must not contact a model service")

    monkeypatch.setattr(providers, "request_decision", never_outbound)
    monkeypatch.setattr(provider, "pinned_endpoint", never_outbound)
    response = await save(client)
    assert response.status_code == 200, response.text
    assert response.json()["key_set"] is True
    assert response.json()["version"] == 1
    assert not {"api_key", "encrypted_key", "organization_id"} & response.json().keys()
    assert TEST_KEY not in response.text
    assert "********" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert (await client.get(BASE)).json() == response.json()
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        stored = (
            await db.execute(text("SELECT encrypted_key FROM forge.ai_provider_settings"))
        ).scalar_one()
        assert stored != TEST_KEY and TEST_KEY not in stored
        assert credentials.decrypt_key(identity["org"], stored).get_secret_value() == TEST_KEY
        for table in ("audit_events", "outbox_events", "idempotency_keys"):
            records = (await db.execute(text(f"SELECT * FROM forge.{table}"))).mappings().all()
            encoded = json.dumps([dict(record) for record in records], default=str)
            assert TEST_KEY not in encoded and stored not in encoded
        audit = (
            await db.execute(
                text("SELECT after FROM forge.audit_events WHERE action='ai.provider.updated'")
            )
        ).scalar_one()
        assert audit == {"version": 1, "key_changed": True, "enabled": True}


async def test_provider_save_idempotency_version_conflict_and_secret_changes(provider_client):
    client, identity = provider_client
    key = uuid4().hex
    first = await save(client, key=key)
    assert first.status_code == 200
    replay = await save(client, key=key)
    assert replay.status_code == 200 and replay.json() == first.json()
    conflict = await save(client, key=key, api_key="another-test-only-key")
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    stale = await save(client)
    assert stale.status_code == 409 and stale.json()["code"] == "AI_PROVIDER_CHANGED"
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        old_key = (
            await db.execute(text("SELECT encrypted_key FROM forge.ai_provider_settings"))
        ).scalar_one()
    retained = await save(client, expected_version=1, api_key=None, model="another-model")
    assert retained.status_code == 200 and retained.json()["version"] == 2
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        assert (
            await db.execute(text("SELECT encrypted_key FROM forge.ai_provider_settings"))
        ).scalar_one() == old_key
    cleared = await save(client, expected_version=2, api_key=None, clear_key=True)
    assert cleared.status_code == 200 and cleared.json()["key_set"] is False


@pytest.mark.parametrize(
    "changed_url,private",
    [
        ("https://different.example.test/v1", False),
        ("https://models.example.test:444/v1", False),
        ("http://models.example.test/v1", True),
    ],
)
async def test_provider_origin_change_requires_explicit_key_choice(
    provider_client, changed_url, private
):
    client, _ = provider_client
    assert (await save(client)).status_code == 200
    response = await save(
        client,
        expected_version=1,
        api_key=None,
        base_url=changed_url,
        allow_private_network=private,
    )
    assert response.status_code == 422 and response.json()["code"] == "AI_PROVIDER_KEY_REQUIRED"
    retained = (await client.get(BASE)).json()
    assert retained["version"] == 1 and retained["base_url"] == "https://models.example.test/v1"
    changed = await save(
        client,
        expected_version=1,
        api_key=None,
        clear_key=True,
        base_url=changed_url,
        allow_private_network=private,
    )
    assert changed.status_code == 200 and changed.json()["key_set"] is False


async def test_provider_rbac_and_tenant_context_cannot_be_client_selected(
    provider_client, identities
):
    client, identity = provider_client
    assert (await save(client)).status_code == 200
    bad = await save(client, expected_version=1, organization_id=str(identities[1]["org"]))
    assert bad.status_code == 422 and TEST_KEY not in bad.text
    grant(identity, {"ai.use"})
    assert (await client.get(BASE)).status_code == 403
    assert (await save(client, expected_version=1)).status_code == 403
    assert (await connect(client)).status_code == 403
    grant(identities[1], {"ai.provider.manage"})
    assert (await login(client, identities[1])).status_code == 200
    other = await client.get(BASE)
    assert other.status_code == 200 and other.json()["version"] == 0
    assert other.json()["key_set"] is False
    assert (await save(client, name="Second organization")).status_code == 200
    async with sessions.begin() as db:
        await set_tenant(db, identities[1]["org"])
        own = (
            await db.execute(text("SELECT organization_id,name FROM forge.ai_provider_settings"))
        ).all()
        assert own == [(identities[1]["org"], "Second organization")]
        with pytest.raises(DBAPIError):
            await db.execute(
                text(
                    "INSERT INTO forge.ai_provider_settings "
                    "(organization_id,name,base_url,model,updated_by) "
                    "VALUES(:org,'bad','https://example.test','test',:actor)"
                ),
                {"org": identity["org"], "actor": identity["user"]},
            )
    async with sessions.begin() as db:
        assert (
            await db.execute(text("SELECT count(*) FROM forge.ai_provider_settings"))
        ).scalar_one() == 0
        flags = (
            await db.execute(
                text(
                    "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
                    "WHERE oid='forge.ai_provider_settings'::regclass"
                )
            )
        ).one()
        assert tuple(flags) == (True, True)


async def test_provider_key_validation_csrf_and_unauthenticated_responses_are_redacted(
    client, identities
):
    response = await save(client)
    assert response.status_code == 401 and TEST_KEY not in response.text
    assert (await connect(client)).status_code == 401
    grant(identities[0], {"ai.provider.manage"})
    assert (await login(client, identities[0])).status_code == 200
    for secret in ("invalid secret whitespace", "bad\nheader", "x" * 1025):
        response = await save(client, api_key=secret)
        assert response.status_code == 422 and secret not in response.text
    bad_combo = await save(client, clear_key=True)
    assert bad_combo.status_code == 422 and TEST_KEY not in bad_combo.text
    csrf = await client.put(
        BASE,
        json=provider_body(),
        headers={"Origin": "https://evil.example", "Idempotency-Key": uuid4().hex},
    )
    assert csrf.status_code == 403 and csrf.json()["code"] == "CSRF_REJECTED"
    assert (await client.get(BASE)).json()["version"] == 0


async def test_provider_connection_test_synthetic_only_replay_and_cooldown(
    provider_client, monkeypatch
):
    client, identity = provider_client
    assert (await save(client)).status_code == 200
    seen = []

    async def fake_decision(messages, connection):
        seen.append((messages, connection))
        assert connection.key.get_secret_value() == TEST_KEY
        assert connection.version == 1
        assert [message.type for message in messages] == ["system", "human"]
        assert identity["code"] not in " ".join(str(message.content) for message in messages)
        # NOWAIT succeeds only after the preflight transaction released its row lock.
        async with sessions.begin() as db:
            await set_tenant(db, identity["org"])
            await db.execute(text("SELECT 1 FROM forge.ai_provider_settings FOR UPDATE NOWAIT"))
        return {"ok": True}

    monkeypatch.setattr(providers, "request_decision", fake_decision)
    key = uuid4().hex
    response = await connect(client, key=key)
    assert response.status_code == 200 and response.json()["ok"] is True
    assert TEST_KEY not in response.text
    replay = await connect(client, key=key)
    assert replay.status_code == 200 and replay.json() == response.json()
    assert len(seen) == 1
    rate = await connect(client)
    assert rate.status_code == 429 and rate.json()["code"] == "AI_PROVIDER_TEST_BUSY"
    assert len(seen) == 1
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.audit_events WHERE action='ai.provider.tested'")
            )
        ).scalar_one() == 1


@pytest.mark.parametrize("change", ["logout", "permission", "disable_user", "configuration"])
async def test_provider_connection_test_reauthenticates_after_network_wait(
    provider_client, monkeypatch, change
):
    client, identity = provider_client
    assert (await save(client)).status_code == 200

    async def fake_decision(messages, connection):
        if change == "logout":
            assert (await client.post("/api/v1/auth/logout")).status_code == 204
        elif change == "permission":
            grant(identity, {"ai.use"})
        elif change == "configuration":
            assert (
                await save(client, expected_version=1, model="changed-during-test")
            ).status_code == 200
        else:
            admin = create_engine(settings().migration_database_url)
            with admin.begin() as db:
                db.execute(
                    text(
                        "UPDATE forge.users SET active=false "
                        "WHERE organization_id=:org AND id=:user"
                    ),
                    identity,
                )
            admin.dispose()
        return {"ok": True}

    monkeypatch.setattr(providers, "request_decision", fake_decision)
    response = await connect(client)
    expected = {"logout": 401, "permission": 403, "disable_user": 401, "configuration": 409}
    assert response.status_code == expected[change], response.text
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.audit_events WHERE action='ai.provider.tested'")
            )
        ).scalar_one() == 0
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.idempotency_keys WHERE operation='ai.provider.test'"
                )
            )
        ).scalar_one() == 0


@pytest.mark.parametrize(
    "bad_decision", [{"ok": 1}, {"ok": "true"}, {"ok": True, "extra": "x"}, {}]
)
async def test_provider_connection_test_requires_exact_boolean_protocol(
    provider_client, monkeypatch, bad_decision
):
    client, _ = provider_client
    assert (await save(client)).status_code == 200

    async def fake_decision(*args):
        return bad_decision

    monkeypatch.setattr(providers, "request_decision", fake_decision)
    response = await connect(client)
    assert response.status_code == 503 and response.json()["code"] == "AI_PROTOCOL_UNSUPPORTED"


async def test_provider_connection_test_disabled_or_stale_version_does_not_call_model(
    provider_client, monkeypatch
):
    client, _ = provider_client
    calls = []

    async def no_call(*args):
        calls.append(args)
        raise AssertionError("No model call for unavailable configuration")

    monkeypatch.setattr(providers, "request_decision", no_call)
    assert (await connect(client)).status_code == 409
    assert (await save(client, enabled=False)).status_code == 200
    response = await connect(client)
    assert response.status_code == 503 and response.json()["code"] == "AI_NOT_CONFIGURED"
    assert (await connect(client, version=2)).status_code == 409
    assert not calls


def test_provider_credentials_are_organization_bound_and_fail_without_plaintext(monkeypatch):
    first, second = uuid4(), uuid4()
    encrypted = credentials.encrypt_key(first, SecretStr(TEST_KEY))
    assert encrypted != credentials.encrypt_key(first, SecretStr(TEST_KEY))
    decrypted = credentials.decrypt_key(first, encrypted)
    assert decrypted.get_secret_value() == TEST_KEY and TEST_KEY not in repr(decrypted)
    for wrong_org, bad_token in ((second, encrypted), (first, "invalid-token")):
        with pytest.raises(Problem) as exc:
            credentials.decrypt_key(wrong_org, bad_token)
        assert exc.value.code == "AI_CREDENTIAL_UNREADABLE"
        assert TEST_KEY not in str(exc.value) and bad_token not in str(exc.value)
    assert credentials.decrypt_key(first, None).get_secret_value() == ""
    monkeypatch.setattr(settings(), "session_secret", "x" * 8)
    with pytest.raises(Problem) as exc:
        credentials.encrypt_key(first, SecretStr(TEST_KEY))
    assert exc.value.code == "AI_ENCRYPTION_UNAVAILABLE"


@pytest.mark.parametrize(
    "base_url,normalized",
    [
        (
            "https://MODEL.Example.test/provider/v1/chat/completions",
            "https://model.example.test/provider/v1",
        ),
        ("http://localhost:11434/v1/", "http://localhost:11434/v1"),
        ("http://[::1]:11434/v1", "http://[::1]:11434/v1"),
    ],
)
def test_provider_input_normalizes_supported_base_urls(base_url, normalized):
    body = ProviderInput.model_validate(
        provider_body(base_url=base_url, allow_private_network=True)
    )
    assert body.base_url == normalized
    assert TEST_KEY not in repr(body)


@pytest.mark.parametrize(
    "base_url",
    [
        "file:///etc/passwd",
        "https://user:password@example.test/v1",
        "https://example.test/v1?api_key=secret",
        "https://example.test/v1#fragment",
        "https://example.test/../private",
        "https://example.test:0/v1",
        "https://metadata.google.internal/v1",
        "https://example.test\\@127.0.0.1/v1",
    ],
)
def test_provider_input_rejects_unsafe_or_credential_bearing_urls(base_url):
    with pytest.raises(ValidationError):
        ProviderInput.model_validate(provider_body(base_url=base_url))


def connection(base_url="https://models.example.test:8443/v1", private=False):
    return ProviderConnection(base_url, "test-model", SecretStr(TEST_KEY), private, 1)


@pytest.mark.parametrize(
    "address,private,allowed",
    [
        ("8.8.8.8", False, True),
        ("2606:4700:4700::1111", False, True),
        ("127.0.0.1", False, False),
        ("127.0.0.1", True, True),
        ("::1", False, False),
        ("::1", True, True),
        ("10.0.0.4", True, True),
        ("fd12:3456::1", True, True),
        ("169.254.169.254", True, False),
        ("::ffff:169.254.169.254", True, False),
        ("100.100.100.200", True, False),
        ("168.63.129.16", True, False),
        ("fd00:ec2::254", True, False),
        ("fd20:ce::254", True, False),
        ("2002:a9fe:a9fe::", True, False),
        ("2001::1", True, False),
        ("192.0.2.1", True, False),
        ("64:ff9b::a9fe:a9fe", True, False),
        ("172.16.0.4", True, True),
        ("192.168.0.4", True, True),
        ("::ffff:192.168.0.4", True, True),
        ("0.0.0.0", True, False),
        ("::", True, False),
        ("224.0.0.1", True, False),
        ("fe80::1", True, False),
        ("not-an-ip", True, False),
        ("2606:4700:4700::1111%untrusted-zone", True, False),
    ],
)
def test_provider_destination_address_policy(address, private, allowed):
    assert network.allowed_address(address, private) is allowed


async def test_provider_dns_is_resolved_once_and_pinned_with_authority_and_sni(monkeypatch):
    loop = asyncio.get_running_loop()
    lookups = []

    async def fake_lookup(host, port, **kwargs):
        lookups.append((host, port, kwargs))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_lookup)
    request_seen = []

    async def reply(request):
        request_seen.append(request)
        assert request.url.host == "8.8.8.8"
        assert request.url.port == 8443
        assert request.headers["Host"] == "models.example.test:8443"
        assert request.extensions["sni_hostname"] == "models.example.test"
        assert request.headers["Authorization"] == "Bearer " + TEST_KEY
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    original_client = httpx.AsyncClient
    options = []

    def fake_client(**kwargs):
        options.append(kwargs)
        return original_client(transport=httpx.MockTransport(reply), **kwargs)

    monkeypatch.setattr(provider.httpx, "AsyncClient", fake_client)
    result = await provider.request_decision([HumanMessage(content="synthetic-only")], connection())
    assert result == {"ok": True}
    assert len(lookups) == len(request_seen) == 1
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False


@pytest.mark.parametrize(
    "addresses,private,scheme,code",
    [
        (["8.8.8.8", "169.254.169.254"], False, "https", "AI_PROVIDER_ADDRESS"),
        (["8.8.8.8", "127.0.0.1"], False, "https", "AI_PROVIDER_ADDRESS"),
        (["8.8.8.8"], True, "http", "AI_PROVIDER_HTTPS_REQUIRED"),
        ([], False, "https", "AI_PROVIDER_ADDRESS"),
    ],
)
async def test_provider_dns_checks_all_answers_and_rejects_public_plaintext(
    monkeypatch, addresses, private, scheme, code
):
    async def fake_lookup(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in addresses]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_lookup)
    with pytest.raises(Problem) as exc:
        await network.pinned_endpoint(
            connection(base_url=f"{scheme}://example.test/v1", private=private)
        )
    assert exc.value.code == code


async def test_provider_dns_failure_is_redacted(monkeypatch):
    async def failed_lookup(*args, **kwargs):
        raise OSError("private resolver diagnostics must not be shown")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", failed_lookup)
    with pytest.raises(Problem) as exc:
        await network.pinned_endpoint(connection())
    assert exc.value.code == "AI_PROVIDER_DNS"
    assert "private resolver" not in str(exc.value)


async def test_provider_entire_call_deadline_includes_dns_and_stream(monkeypatch):
    monkeypatch.setattr(settings(), "ai_timeout_seconds", 0.01)
    cancelled = asyncio.Event()

    async def blocked_lookup(connection):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(provider, "pinned_endpoint", blocked_lookup)
    with pytest.raises(Problem) as exc:
        await provider.request_decision([HumanMessage(content="synthetic-only")], connection())
    assert exc.value.code == "AI_PROVIDER_TIMEOUT" and cancelled.is_set()


async def test_provider_local_ipv6_pin_remains_bracketed_and_keeps_original_host(monkeypatch):
    async def fake_lookup(host, port, **kwargs):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_lookup)
    url, authority, hostname = await network.pinned_endpoint(
        connection(base_url="http://localhost:11434/v1", private=True)
    )
    assert url == "http://[::1]:11434/v1/chat/completions"
    assert authority == "localhost:11434" and hostname == "localhost"


async def test_provider_concurrent_connection_tests_make_one_outbound_call(
    provider_client, monkeypatch
):
    client, _ = provider_client
    assert (await save(client)).status_code == 200
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def fake_decision(*args):
        calls.append(args)
        started.set()
        await finish.wait()
        return {"ok": True}

    monkeypatch.setattr(providers, "request_decision", fake_decision)
    first_task = asyncio.create_task(connect(client))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        second = await connect(client)
        assert second.status_code == 429 and second.json()["code"] == "AI_PROVIDER_TEST_BUSY"
        assert len(calls) == 1
    finally:
        finish.set()
        first = await first_task
    assert first.status_code == 200


async def test_provider_failed_audit_rolls_back_credentials_and_receipt(
    provider_client, monkeypatch
):
    client, identity = provider_client

    async def reject_audit(*args):
        raise Problem(503, "TEST_AUDIT_FAILED", "synthetic atomicity failure")

    monkeypatch.setattr(providers, "record_mutation", reject_audit)
    response = await save(client)
    assert response.status_code == 503 and TEST_KEY not in response.text
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        assert (
            await db.execute(text("SELECT count(*) FROM forge.ai_provider_settings"))
        ).scalar_one() == 0
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.idempotency_keys WHERE operation='ai.provider.save'"
                )
            )
        ).scalar_one() == 0


async def test_provider_entire_call_deadline_cancels_slow_response_body(monkeypatch):
    monkeypatch.setattr(settings(), "ai_timeout_seconds", 0.02)
    cancelled = asyncio.Event()

    async def pinned(*args):
        return "https://8.8.8.8/v1/chat/completions", "models.example.test", "models.example.test"

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                yield b"{"
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    async def reply(request):
        return httpx.Response(200, stream=SlowStream())

    original_client = httpx.AsyncClient
    monkeypatch.setattr(provider, "pinned_endpoint", pinned)
    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(reply), **kwargs),
    )
    with pytest.raises(Problem) as exc:
        await provider.request_decision([HumanMessage(content="synthetic-only")], connection())
    assert exc.value.code == "AI_PROVIDER_TIMEOUT" and cancelled.is_set()


async def test_provider_concurrent_save_cannot_overwrite_a_newer_version(provider_client):
    client, identity = provider_client
    assert (await save(client)).status_code == 200
    results = await asyncio.gather(
        save(client, expected_version=1, model="candidate-a"),
        save(client, expected_version=1, model="candidate-b"),
    )
    assert sorted(result.status_code for result in results) == [200, 409]
    winner = next(result.json() for result in results if result.status_code == 200)
    current = (await client.get(BASE)).json()
    assert current == winner and current["version"] == 2
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.audit_events WHERE action='ai.provider.updated'")
            )
        ).scalar_one() == 2


@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate", "identity, gzip"])
async def test_provider_rejects_compression_before_reading_or_expanding_body(monkeypatch, encoding):
    consumed = []

    async def pinned(*args):
        return "https://8.8.8.8/v1/chat/completions", "models.example.test", "models.example.test"

    class UntrustedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            consumed.append(True)
            yield b"untrusted compressed data must never be read or decoded"

    async def reply(request):
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(200, headers={"Content-Encoding": encoding}, stream=UntrustedStream())

    original_client = httpx.AsyncClient
    monkeypatch.setattr(provider, "pinned_endpoint", pinned)
    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(reply), **kwargs),
    )
    with pytest.raises(Problem) as exc:
        await provider.request_decision([HumanMessage(content="synthetic-only")], connection())
    assert exc.value.code == "AI_PROVIDER_ENCODING"
    assert consumed == []
