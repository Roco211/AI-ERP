from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024, repr=False)

    @field_validator("email")
    @classmethod
    def email_shape(cls, value: str) -> str:
        if "@" not in value or any(c.isspace() for c in value):
            raise ValueError("Invalid email")
        return value.lower()


class Profile(BaseModel):
    user_id: UUID
    organization_id: UUID
    organization_code: str
    organization_name: str
    email: str
    display_name: str
    permissions: list[str]


class LoginResult(BaseModel):
    authenticated: bool = True
