from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from forge_erp.modules.catalog.domain.schemas import Amount, InputModel

Kind = Literal["OPENING", "ADJUSTMENT", "TRANSFER", "STOCKTAKE"]
Status = Literal["DRAFT", "POSTED", "REVERSED"]


class InventoryLineInput(InputModel):
    product_id: UUID
    unit_id: UUID
    qty: Amount
    direction: Literal["IN", "OUT"] = "IN"
    input_unit_cost: Amount | None = None


class InventoryDraftFields(InputModel):
    warehouse_id: UUID
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    lines: Annotated[list[InventoryLineInput], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def unique(self):
        if len({x.product_id for x in self.lines}) != len(self.lines):
            raise ValueError("Each product must appear once")
        if getattr(self, "target_warehouse_id", None) == self.warehouse_id:
            raise ValueError("Transfer warehouses must differ")
        return self


class InventoryDraftInput(InventoryDraftFields):
    target_warehouse_id: UUID | None = None


class OpeningInput(InventoryDraftFields):
    target_warehouse_id: None = None


class AdjustmentInput(InventoryDraftFields):
    target_warehouse_id: None = None


class TransferInput(InventoryDraftFields):
    target_warehouse_id: UUID


class StocktakeInput(InventoryDraftFields):
    target_warehouse_id: None = None


class InventoryDraftUpdate(InventoryDraftInput):
    expected_version: Annotated[int, Field(ge=1)]


class InventoryVersion(InputModel):
    expected_version: Annotated[int, Field(ge=1)]


class InventoryReverse(InventoryVersion):
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class InventoryReceipt(BaseModel):
    id: UUID
    status: Status
    version: int
    request_id: str


class InventoryLineRead(BaseModel):
    id: UUID
    product_id: UUID
    unit_id: UUID
    product_label: str
    unit_label: str
    qty: Decimal
    unit_to_base_factor: Decimal
    base_qty: Decimal
    conversion_version: int
    direction: Literal["IN", "OUT"]
    input_unit_cost: Decimal | None = None
    baseline_qty: Decimal
    baseline_version: int


class InventoryDocumentRead(BaseModel):
    id: UUID
    number: str
    type: (
        Kind
        | Literal[
            "PURCHASE_RECEIPT",
            "PURCHASE_RETURN",
            "SALES_RESERVATION",
            "SALES_SHIPMENT",
            "SALES_RETURN",
        ]
    )
    status: Status
    version: int
    reason: str
    warehouse_id: UUID
    target_warehouse_id: UUID | None = None
    warehouse_name: str
    target_warehouse_name: str | None = None
    created_at: datetime
    posted_at: datetime | None = None
    reversed_at: datetime | None = None
    reversal_id: UUID | None = None
    lines: list[InventoryLineRead] = Field(default_factory=list)


class InventoryDocumentsPage(BaseModel):
    items: list[InventoryDocumentRead]
    total: int
    page: int
    page_size: int


class InventoryBalanceRead(BaseModel):
    product_id: UUID
    warehouse_id: UUID
    product_name: str
    sku: str
    warehouse_name: str
    unit_name: str
    on_hand_qty: Decimal
    reserved_qty: Decimal
    available_qty: Decimal
    inventory_value: Decimal | None = None
    avg_unit_cost: Decimal | None = None
    version: int


class InventoryBalancesPage(BaseModel):
    items: list[InventoryBalanceRead]
    total: int
    page: int
    page_size: int


class InventoryMovementRead(BaseModel):
    id: UUID
    warehouse_id: UUID
    product_id: UUID
    document_id: UUID
    document_number: str
    document_type: str
    sales_order_id: UUID | None = Field(
        default=None, description="Originating sales order; requires sales.read"
    )
    product_label: str
    unit_label: str
    warehouse_name: str
    sequence: int
    kind: str
    base_qty: Decimal
    reserved_qty_delta: Decimal
    value_delta: Decimal | None = None
    before_avg_cost: Decimal | None = None
    after_avg_cost: Decimal | None = None
    rounding_delta: Decimal | None = None
    original_movement_id: UUID | None = None
    reversal_id: UUID | None = None
    created_at: datetime


class InventoryMovementsPage(BaseModel):
    items: list[InventoryMovementRead]
    next_cursor: str | None = None
    cutoff: str


class LowStockRead(BaseModel):
    product_id: UUID
    sku: str
    product_name: str
    unit_name: str
    available_qty: Decimal
    min_stock_qty: Decimal


class LowStockPage(BaseModel):
    items: list[LowStockRead]
    total: int
    page: int
    page_size: int


type DraftInput = (
    InventoryDraftInput | OpeningInput | AdjustmentInput | TransferInput | StocktakeInput
)
