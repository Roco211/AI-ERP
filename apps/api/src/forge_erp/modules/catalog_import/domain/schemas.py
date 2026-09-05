"""Public import contracts; clients never submit tenant, actor or resolved catalog IDs."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ResourceName = Literal[
    "categories",
    "brands",
    "units",
    "customers",
    "suppliers",
    "warehouses",
    "products",
    "product-units",
    "product-prices",
    "supplier-products",
]
Mode = Literal["CREATE_ONLY", "UPDATE_EXISTING"]
RowStatus = Literal["READY", "INVALID", "PENDING", "SUCCEEDED", "FAILED"]
BatchStatus = Literal[
    "PREVIEW_READY",
    "PREVIEW_INVALID",
    "QUEUED",
    "RUNNING",
    "COMPLETED",
    "PARTIAL_FAILED",
    "FAILED",
    "BLOCKED",
    "EXPIRED",
]
CellValue = str | bool | None


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfirmInput(Input):
    expected_version: int = Field(ge=1)
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class RetryInput(Input):
    expected_version: int = Field(ge=1)
    row_ids: list[UUID] = Field(min_length=1, max_length=10000)


class ImportErrorRead(BaseModel):
    column: str
    code: str
    message: str


class BatchReceipt(BaseModel):
    id: UUID
    status: BatchStatus
    version: int
    request_id: str


class BatchRead(BaseModel):
    id: UUID
    resource: ResourceName
    mode: Mode
    filename: str
    worksheet: str
    preview_hash: str
    version: int
    status: BatchStatus
    created_by: UUID
    created_at: datetime
    expires_at: datetime
    confirmed_at: datetime | None
    total: int
    ready: int
    invalid: int
    pending: int
    succeeded: int
    failed: int
    body_available: bool
    is_creator: bool
    can_confirm: bool
    can_retry: bool


class BatchesPage(BaseModel):
    items: list[BatchRead]
    page: int
    page_size: int
    total: int


class RowRead(BaseModel):
    id: UUID
    row_no: int
    action: Literal["CREATE", "UPDATE"]
    status: RowStatus
    raw_values: dict[str, CellValue] | None
    cleaned_values: dict[str, CellValue] | None
    errors: list[ImportErrorRead] | None
    error_code: str | None
    retryable: bool
    expected_target_id: UUID | None
    expected_version: int | None
    target_id: UUID | None
    target_version: int | None
    attempts: int
    completed_at: datetime | None


class RowsPage(BaseModel):
    items: list[RowRead]
    page: int
    page_size: int
    total: int
