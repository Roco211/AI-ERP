"""Local-only Ollama adapter. Vectors are numerical search features, never ERP amounts."""

import json
import math

import httpx

from forge_erp.core.config import settings

DIMENSIONS = 1024


class EmbeddingUnavailable(Exception):
    pass


def vector_literal(value: object) -> str:
    if not isinstance(value, list) or len(value) != DIMENSIONS:
        raise EmbeddingUnavailable("Invalid embedding dimensions")
    if any(type(x) not in (float, int) or not math.isfinite(x) for x in value):
        raise EmbeddingUnavailable("Invalid embedding values")
    norm = math.sqrt(sum(x * x for x in value))
    if not math.isfinite(norm) or norm < 1e-12:
        raise EmbeddingUnavailable("Zero or unbounded embedding")
    return json.dumps([x / norm for x in value])


def model_identity() -> str:
    cfg = settings()
    return f"{cfg.embedding_model}@{cfg.embedding_model_digest}"


async def embed(value: str, *, indexing: bool = False) -> str:
    cfg = settings()
    if not cfg.embedding_enabled:
        raise EmbeddingUnavailable("Local embeddings are disabled")
    timeout = 60.0 if indexing else 2.0
    try:
        async with httpx.AsyncClient(
            base_url=cfg.embedding_url, timeout=timeout, trust_env=False, follow_redirects=False
        ) as client:
            tags = await client.get("/api/tags")
            tags.raise_for_status()
            if not any(
                m.get("name") == cfg.embedding_model
                and m.get("digest") == cfg.embedding_model_digest
                for m in tags.json().get("models", [])
            ):
                raise EmbeddingUnavailable("Pinned local model is unavailable")
            response = await client.post(
                "/api/embed",
                json={
                    "model": cfg.embedding_model,
                    "input": value,
                    "truncate": False,
                    "keep_alive": "30m",
                    "options": {"num_thread": 4},
                },
            )
            response.raise_for_status()
            rows = response.json().get("embeddings")
            if not isinstance(rows, list) or len(rows) != 1:
                raise EmbeddingUnavailable("Invalid embedding response")
            return vector_literal(rows[0])
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise EmbeddingUnavailable("Local model request failed") from exc
