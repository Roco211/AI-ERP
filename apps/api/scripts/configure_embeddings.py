"""Enable local embeddings only after verifying the downloaded model's digest."""

from pathlib import Path

import httpx

from forge_erp.core.config import settings


def main() -> None:
    cfg = settings()
    with httpx.Client(base_url=cfg.embedding_url, timeout=10, trust_env=False) as client:
        response = client.get("/api/tags")
        response.raise_for_status()
        model = next(
            (m for m in response.json()["models"] if m["name"] == cfg.embedding_model), None
        )
    if model is None:
        raise RuntimeError("Pull bge-m3:567m with make semantic-pull first")
    # Reuse Settings validation before persisting any configuration.
    settings_type = type(cfg)
    settings_type(embedding_enabled=True, embedding_model_digest=model["digest"])
    path = Path(__file__).resolve().parents[3] / ".env"
    if not path.exists():
        raise RuntimeError("Run make env first")
    updates = {
        "EMBEDDING_ENABLED": "true",
        "EMBEDDING_URL": cfg.embedding_url,
        "EMBEDDING_MODEL": cfg.embedding_model,
        "EMBEDDING_MODEL_DIGEST": model["digest"],
    }
    lines = path.read_text().splitlines()
    lines = [line for line in lines if line.partition("=")[0] not in updates]
    path.write_text(
        "\n".join(lines) + "\n" + "\n".join(f"{k}={v}" for k, v in updates.items()) + "\n"
    )
    print(f"Enabled local {cfg.embedding_model}; pinned digest {model['digest']}.")
    print("Restart API/worker/beat after configuring, then run make semantic-rebuild.")


if __name__ == "__main__":
    main()
