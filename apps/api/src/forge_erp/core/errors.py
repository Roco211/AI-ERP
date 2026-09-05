from typing import Any

from pydantic import BaseModel, Field


class Problem(Exception):
    def __init__(self, status: int, code: str, detail: str):
        self.status, self.code, self.detail = status, code, detail
        super().__init__(detail)


class ProblemDetails(BaseModel):
    type: str
    title: str
    status: int
    code: str
    detail: str
    request_id: str
    context: dict[str, Any] = Field(default_factory=dict)
