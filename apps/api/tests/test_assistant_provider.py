import json
import logging

import httpx
import pytest
import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import SecretStr, ValidationError

from forge_erp.core.config import Settings
from forge_erp.core.errors import Problem
from forge_erp.core.observability import configure_logging
from forge_erp.modules.assistant.domain.providers import ProviderConnection
from forge_erp.modules.assistant.infrastructure import provider


def connection(secret="test-only-key"):
    return ProviderConnection(
        "https://api.commandcode.ai/provider/v1",
        "deepseek-v4-flash-vision-exp",
        SecretStr(secret),
        False,
        1,
    )


@pytest.fixture(autouse=True)
def pin_test_connection(monkeypatch):
    async def pin(conn):
        return conn.endpoint, "api.commandcode.ai", "api.commandcode.ai"

    monkeypatch.setattr(provider, "pinned_endpoint", pin)


def test_provider_connection_masks_secret_and_timeout_is_bounded():
    assert "private-test-credential" not in repr(connection("private-test-credential"))
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ai_timeout_seconds=99)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"null",
        b"[]",
        b'{"choices":[]}',
        b'{"choices":{}}',
        b'{"choices":[null]}',
        b'{"choices":[[]]}',
        b'{"choices":["private-provider-payload"]}',
        b'{"choices":[{"message":[]}]}',
        b'{"choices":[{"message":null}]}',
        b'{"choices":[{"message":"private-provider-payload"}]}',
        b'{"choices":[{"message":{"content":"[]"}}]}',
        b'{"choices":[{"message":{"content":"{}","tool_calls":[{}]}}]}',
        b'{"choices":[{"message":{"content":null}}]}',
    ],
)
def test_invalid_model_response_is_sanitized(body):
    with pytest.raises(Problem) as exc:
        provider.parse_decision(body)
    assert exc.value.code == "AI_INVALID_RESPONSE"
    assert "choices" not in exc.value.detail


def test_valid_envelope_and_exact_fenced_json():
    decision = {"action": "answer", "code": "results", "evidence_ids": ["fact-1"]}
    for content in (json.dumps(decision), "```json\n" + json.dumps(decision) + "\n```"):
        raw = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        assert provider.parse_decision(raw) == decision


@pytest.mark.parametrize("content", ['{"action":', "{}", None])
def test_token_truncation_has_an_explicit_sanitized_failure(content):
    raw = json.dumps(
        {"choices": [{"finish_reason": "length", "message": {"content": content}}]}
    ).encode()
    with pytest.raises(Problem) as exc:
        provider.parse_decision(raw)
    assert exc.value.status == 503
    assert exc.value.code == "AI_RESPONSE_TRUNCATED"
    assert "截断" in exc.value.detail
    assert "action" not in exc.value.detail


def test_decision_character_bound_remains_independent_of_token_budget():
    content = json.dumps({"data": "x" * provider.MAX_DECISION_CHARS})
    with pytest.raises(Problem) as exc:
        provider.parse_decision(
            json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        )
    assert exc.value.code == "AI_INVALID_RESPONSE"


async def test_missing_key_makes_no_network_call(monkeypatch):
    with pytest.raises(Problem) as exc:
        await provider.request_decision([HumanMessage(content="库存")])
    assert exc.value.code == "AI_NOT_CONFIGURED"


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "AI_PROVIDER_AUTH"),
        (403, "AI_PROVIDER_AUTH"),
        (429, "AI_PROVIDER_RATE_LIMIT"),
        (500, "AI_PROVIDER_UNAVAILABLE"),
        (302, "AI_PROVIDER_UNAVAILABLE"),
    ],
)
async def test_provider_http_errors_do_not_echo_credentials(monkeypatch, status, code):
    secret = "test-provider-secret-never-echo"
    real_client = httpx.AsyncClient

    def handler(request):
        assert str(request.url) == connection().endpoint
        assert request.extensions["sni_hostname"] == "api.commandcode.ai"
        assert request.headers["Authorization"] == "Bearer " + secret
        return httpx.Response(status, text=secret)

    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(Problem) as exc:
        await provider.request_decision(
            [SystemMessage(content="protocol"), HumanMessage(content="x")], connection(secret)
        )
    assert exc.value.code == code
    assert secret not in str(exc.value) + exc.value.detail


async def test_transport_sends_selected_model_and_parses_decision(monkeypatch):
    real_client = httpx.AsyncClient

    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "deepseek-v4-flash-vision-exp"
        assert body["stream"] is False
        assert body["max_tokens"] == 4096
        assert "organization_id" not in body and "tools" not in body
        assert body["messages"] == [{"role": "user", "content": "库存"}]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"action":"query"}'}}]}
        )

    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    assert await provider.request_decision([HumanMessage(content="库存")], connection()) == {
        "action": "query"
    }


@pytest.mark.parametrize("mode", ["timeout", "oversize", "network"])
async def test_bounded_transport_failures(monkeypatch, mode):
    real_client = httpx.AsyncClient

    def handler(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("sensitive upstream info", request=request)
        if mode == "network":
            raise httpx.ConnectError("sensitive upstream info", request=request)
        return httpx.Response(200, content=b"x" * (provider.MAX_RESPONSE_BYTES + 1))

    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(Problem) as exc:
        await provider.request_decision([HumanMessage(content="x")], connection())
    assert (
        exc.value.code
        == {
            "timeout": "AI_PROVIDER_TIMEOUT",
            "network": "AI_PROVIDER_CONNECTION",
            "oversize": "AI_RESPONSE_LIMIT",
        }[mode]
    )
    assert "sensitive" not in exc.value.detail


@pytest.mark.parametrize(
    "envelope,code",
    [
        ({"choices": [{"message": []}]}, "AI_INVALID_RESPONSE"),
        ({"choices": [[]]}, "AI_INVALID_RESPONSE"),
        (
            {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
            "AI_RESPONSE_TRUNCATED",
        ),
    ],
)
async def test_nested_malformed_or_truncated_transport_response_is_a_problem(
    monkeypatch, envelope, code
):
    real_client = httpx.AsyncClient
    calls = 0

    def reply(request):
        nonlocal calls
        calls += 1
        assert json.loads(request.content)["max_tokens"] == 4096
        return httpx.Response(200, json=envelope)

    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(reply)),
    )
    with pytest.raises(Problem) as exc:
        await provider.request_decision([HumanMessage(content="synthetic-only")], connection())
    assert exc.value.code == code
    assert calls == 1  # Diagnostics never silently retry or make additional paid requests.


async def test_larger_output_budget_keeps_request_byte_limit_before_network(monkeypatch):
    called = False

    async def pin(_):
        nonlocal called
        called = True
        raise AssertionError("The oversized request must be rejected before DNS")

    monkeypatch.setattr(provider, "pinned_endpoint", pin)
    with pytest.raises(Problem) as exc:
        await provider.request_decision(
            [HumanMessage(content="x" * provider.MAX_REQUEST_BYTES)], connection()
        )
    assert exc.value.code == "AI_CONTEXT_LIMIT"
    assert not called


async def test_actual_http_requests_never_log_provider_path_credentials(monkeypatch, caplog):
    real_client = httpx.AsyncClient
    old_levels = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    sensitive = "private-path-token-never-log"
    conn = ProviderConnection(
        f"https://models.example.test/{sensitive}/v1",
        "synthetic-model",
        SecretStr("private-bearer-token-never-log"),
        False,
        1,
    )

    def reply(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    try:
        with caplog.at_level(logging.DEBUG):
            logging.getLogger("httpx").setLevel(logging.INFO)
            logging.getLogger("httpcore").setLevel(logging.DEBUG)
            async with real_client(transport=httpx.MockTransport(reply)) as client:
                await client.get("https://models.example.test/harmless-control")
            assert any(
                record.name == "httpx" and "harmless-control" in record.getMessage()
                for record in caplog.records
            )
            caplog.clear()
            configure_logging()
            monkeypatch.setattr(
                provider.httpx,
                "AsyncClient",
                lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(reply)),
            )
            assert await provider.request_decision(
                [HumanMessage(content="synthetic-only")], conn
            ) == {"ok": True}
            logging.getLogger("httpcore.connection").debug("connect_tcp host=%s", sensitive)
            structlog.get_logger("forge.provider-test").info(
                "provider_transport_completed", request_id="synthetic-log-test"
            )
            assert not any(
                record.name.startswith(("httpx", "httpcore")) for record in caplog.records
            )
            assert sensitive not in caplog.text
            assert conn.key.get_secret_value() not in caplog.text
            record = next(
                record for record in caplog.records if record.name == "forge.provider-test"
            )
            assert json.loads(record.getMessage())["request_id"] == "synthetic-log-test"
    finally:
        for name, level in old_levels.items():
            logging.getLogger(name).setLevel(level)
