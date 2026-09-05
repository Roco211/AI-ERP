from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[5] / ".env", extra="ignore"
    )
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://forge_app:unset@127.0.0.1:55438/forge"
    migration_database_url: str = ""
    redis_url: str = "redis://127.0.0.1:56379/0"
    session_secret: str = Field(default="", repr=False)
    cookie_secure: bool = True
    web_origin: str = "http://localhost:3100"
    session_ttl_seconds: int = 28800
    sentry_dsn: str = ""
    business_timezone: str = "Asia/Shanghai"

    embedding_enabled: bool = False
    embedding_url: str = "http://127.0.0.1:11438"
    embedding_model: str = "bge-m3:567m"
    embedding_model_digest: str = ""
    embedding_min_similarity: float = Field(default=0.55, ge=0, le=1)

    @model_validator(mode="after")
    def secure_production(self) -> Settings:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(self.business_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Business timezone must be a valid IANA timezone") from exc
        if self.app_env == "production" and (
            not self.cookie_secure or not self.web_origin.startswith("https://")
        ):
            raise ValueError("Production requires HTTPS and Secure cookies")
        import re
        from urllib.parse import urlsplit

        endpoint = urlsplit(self.embedding_url)
        if (
            endpoint.scheme != "http"
            or endpoint.hostname not in {"127.0.0.1", "localhost", "::1", "ollama"}
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
            or endpoint.path not in {"", "/"}
        ):
            raise ValueError("Embedding endpoint must be a local Ollama runtime")
        if self.embedding_enabled and not re.fullmatch(
            r"(?:sha256:)?[0-9a-f]{64}", self.embedding_model_digest
        ):
            raise ValueError("Enabled embeddings require a pinned local model digest")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
