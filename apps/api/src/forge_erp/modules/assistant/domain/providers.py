from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from forge_erp.modules.catalog.domain.schemas import InputModel


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if value.endswith("/chat/completions"):
        value = value.removesuffix("/chat/completions")
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or "\\" in value
        or any(c.isspace() for c in value)
        or parts.port == 0
    ):
        raise ValueError("请填写不含密钥或查询参数的 HTTP(S) API 基础地址")
    host = parts.hostname.encode("idna").decode().lower()
    if host in {"metadata.google.internal", "metadata", "instance-data"}:
        raise ValueError("不允许的模型服务地址")
    authority = f"[{host}]" if ":" in host else host
    if parts.port:
        authority += f":{parts.port}"
    path = parts.path.rstrip("/")
    if "//" in path or any(piece in {".", ".."} for piece in path.split("/")):
        raise ValueError("请填写规范的 API 基础路径")
    return urlunsplit((parts.scheme, authority, path, "", ""))


class ProviderInput(InputModel):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    base_url: Annotated[str, Field(min_length=1, max_length=2048)]
    model: Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:/-]+$")]
    enabled: bool
    allow_private_network: bool = False
    expected_version: Annotated[int, Field(ge=0)]
    api_key: SecretStr | None = Field(default=None, repr=False)
    clear_key: bool = False

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        return normalize_base_url(value)

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            raw = value.get_secret_value()
            if not 1 <= len(raw) <= 1024 or any(ord(c) < 33 or ord(c) > 126 for c in raw):
                raise ValueError("密钥格式无效")
        return value

    @model_validator(mode="after")
    def coherent(self):
        if self.api_key is not None and self.clear_key:
            raise ValueError("不能同时填写和清除密钥")
        if self.base_url.startswith("http:") and not self.allow_private_network:
            raise ValueError("HTTP 地址仅用于明确启用的本机/内网模型")
        return self


class ProviderRead(BaseModel):
    name: str = "CommandCode"
    base_url: str = "https://api.commandcode.ai/provider/v1"
    model: str = "deepseek-v4-flash-vision-exp"
    enabled: bool = False
    allow_private_network: bool = False
    key_set: bool = False
    version: int = 0
    updated_at: datetime | None = None


class ProviderTestInput(InputModel):
    expected_version: Annotated[int, Field(ge=1)]


class ProviderTestRead(BaseModel):
    ok: bool
    message: str
    model: str
    version: int


@dataclass(frozen=True)
class ProviderConnection:
    base_url: str
    model: str
    key: SecretStr
    allow_private_network: bool
    version: int

    @property
    def endpoint(self) -> str:
        return self.base_url + "/chat/completions"
