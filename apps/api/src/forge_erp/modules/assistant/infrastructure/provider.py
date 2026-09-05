"""Bounded selected-provider transport. No SDK callbacks capture business messages."""

import asyncio
import json
from typing import Any

import httpx
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda

from forge_erp.core.config import settings
from forge_erp.core.errors import Problem
from forge_erp.modules.assistant.domain.providers import ProviderConnection
from forge_erp.modules.assistant.infrastructure.network import pinned_endpoint

MAX_RESPONSE_BYTES = 128_000
MAX_REQUEST_BYTES = 128_000
MAX_OUTPUT_TOKENS = 4096
MAX_DECISION_CHARS = 16_000


def wire_messages(messages: list[BaseMessage]) -> list[dict[str, str]]:
    result = []
    for message in messages:
        if not isinstance(message.content, str):
            raise Problem(422, "AI_TEXT_ONLY", "助手本期只支持文本输入")
        if isinstance(message, SystemMessage):
            role = "system"
        elif isinstance(message, HumanMessage):
            role = "user"
        else:
            raise Problem(422, "AI_MESSAGE_ROLE", "不支持的助手消息类型")
        result.append({"role": role, "content": message.content})
    return result


def parse_decision(raw: bytes) -> dict[str, Any]:
    try:
        envelope = json.loads(raw)
        if not isinstance(envelope, dict):
            raise ValueError
        choices = envelope["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ValueError
        if choice.get("finish_reason") == "length":
            raise Problem(503, "AI_RESPONSE_TRUNCATED", "模型输出被截断，请缩小请求范围后重试")
        message = choice["message"]
        if not isinstance(message, dict) or message.get("tool_calls"):
            raise ValueError
        content = message["content"]
        if not isinstance(content, str) or len(content) > MAX_DECISION_CHARS:
            raise ValueError
        content = content.strip()
        if content.startswith("```json\n") and content.endswith("\n```"):
            content = content[8:-4]
        decision = json.loads(content)
        if not isinstance(decision, dict):
            raise ValueError
        return decision
    except KeyError, IndexError, TypeError, ValueError, RecursionError:
        raise Problem(503, "AI_INVALID_RESPONSE", "模型未返回有效决策，请重试或补充条件") from None


async def _request_decision(
    messages: list[BaseMessage], connection: ProviderConnection | None = None
) -> dict[str, Any]:
    cfg = settings()
    if connection is None:
        raise Problem(503, "AI_NOT_CONFIGURED", "对话模型尚未配置，普通 ERP 功能可继续使用")
    payload = {
        "model": connection.model,
        "messages": wire_messages(messages),
        "stream": False,
        "max_tokens": MAX_OUTPUT_TOKENS,
    }
    body = json.dumps(payload, ensure_ascii=False).encode()
    if len(body) > MAX_REQUEST_BYTES:
        raise Problem(422, "AI_CONTEXT_LIMIT", "本轮资料过多，请缩小查询范围或新建会话")
    url, authority, hostname = await pinned_endpoint(connection)
    headers = {"Host": authority, "Content-Type": "application/json", "Accept-Encoding": "identity"}
    if connection.key.get_secret_value():
        headers["Authorization"] = "Bearer " + connection.key.get_secret_value()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(cfg.ai_timeout_seconds), follow_redirects=False, trust_env=False
        ) as client:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                content=body,
                extensions={"sni_hostname": hostname},
            ) as response:
                if response.status_code in (401, 403):
                    raise Problem(503, "AI_PROVIDER_AUTH", "模型服务认证失败，请检查设置中的密钥")
                if response.status_code == 429:
                    raise Problem(429, "AI_PROVIDER_RATE_LIMIT", "模型服务暂时限流，请稍后重试")
                if response.status_code != 200:
                    raise Problem(
                        503, "AI_PROVIDER_UNAVAILABLE", "指定模型服务暂不可用，请检查模型配置后重试"
                    )
                # httpx decodes before aiter_bytes yields; reject compression
                # before body consumption so a small compressed payload cannot
                # expand beyond the application's response memory budget.
                if response.headers.get("Content-Encoding", "").strip().lower() not in {
                    "",
                    "identity",
                }:
                    raise Problem(503, "AI_PROVIDER_ENCODING", "模型服务返回了不支持的压缩响应")
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(chunks) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise Problem(503, "AI_RESPONSE_LIMIT", "模型返回内容过大，请缩小请求范围")
                    chunks.extend(chunk)
    except httpx.TimeoutException:
        raise Problem(503, "AI_PROVIDER_TIMEOUT", "模型响应超时，本轮未自动执行业务操作") from None
    except httpx.HTTPError:
        raise Problem(503, "AI_PROVIDER_CONNECTION", "无法连接指定模型服务，请稍后重试") from None
    return parse_decision(bytes(chunks))


async def request_decision(
    messages: list[BaseMessage], connection: ProviderConnection | None = None
) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(
            _request_decision(messages, connection), timeout=settings().ai_timeout_seconds
        )
    except TimeoutError:
        raise Problem(503, "AI_PROVIDER_TIMEOUT", "模型响应超时，请稍后重试") from None


# A LangChain component with no automatic retry or implicit fallback provider.
decision_runnable = RunnableLambda(request_decision, name="erp_model_decision")
