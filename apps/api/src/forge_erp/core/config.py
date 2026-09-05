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

    @model_validator(mode="after")
    def secure_production(self) -> Settings:
        if self.app_env == "production" and (
            not self.cookie_secure or not self.web_origin.startswith("https://")
        ):
            raise ValueError("Production requires HTTPS and Secure cookies")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
