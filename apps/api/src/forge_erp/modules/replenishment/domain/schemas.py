from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from forge_erp.modules.catalog.domain.schemas import Amount, InputModel
from forge_erp.modules.purchasing.domain.schemas import PurchaseReceipt, Reason

Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Suggestion(BaseModel):
    product_id: UUID
    sku: str
    name: str
    product_version: int
    category_id: UUID
    base_unit_id: UUID
    base_unit_name: str
    default_purchase_unit_id: UUID
    preferred_supplier_id: UUID | None
    preferred_supplier_name: str | None
    lead_days: int | None
    available_qty: Decimal
    open_purchase_qty: Decimal
    shipped_qty: Decimal
    returned_qty: Decimal
    net_sales_qty: Decimal
    daily_sales_qty: Decimal
    safety_stock_qty: Decimal
    minimum_reorder_qty: Decimal
    reorder_point: Decimal
    target_stock: Decimal
    inventory_position: Decimal
    gap: Decimal
    candidate: bool
    suggested_base_qty: Decimal
    days_of_stock: Decimal | None
    reasons: list[str]
    basis_hash: str


class Window(BaseModel):
    as_of: datetime
    business_timezone: str
    window_start: datetime
    window_end: datetime
    algorithm_version: str


class SuggestionsPage(Window):
    items: list[Suggestion]
    total: int
    page: int
    page_size: int


class SuggestionCounts(BaseModel):
    candidate_count: int
    suggested_count: int


class PreviewLineInput(InputModel):
    product_id: UUID
    basis_hash: Hash
    unit_id: UUID
    qty: Amount | None = None
    quantity_reason: Reason | None = None
    price_source: Literal["MANUAL", "HISTORY"] = "MANUAL"
    unit_price: Amount | None = None

    @model_validator(mode="after")
    def explicit_changes(self):
        if self.qty is not None and (self.qty <= 0 or self.quantity_reason is None):
            raise ValueError("手工数量必须大于零且填写调整原因")
        if self.price_source == "HISTORY" and self.unit_price is not None:
            raise ValueError("历史参考价由服务器确定；手工单价请选择 MANUAL")
        return self


class PurchasePreviewInput(InputModel):
    supplier_id: UUID
    warehouse_id: UUID
    reason: Reason
    lines: Annotated[list[PreviewLineInput], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def unique(self):
        if len({line.product_id for line in self.lines}) != len(self.lines):
            raise ValueError("每个商品只能出现一次")
        return self


class PurchaseCreationInput(PurchasePreviewInput):
    confirmation_hash: Hash


class HistoricalPrice(BaseModel):
    document_id: UUID
    document_number: str
    line_id: UUID
    posted_at: datetime
    unit_id: UUID
    unit_label: str
    unit_price: Decimal
    unit_to_base_factor: Decimal
    conversion_version: int
    selected_unit_price: Decimal | None


class PreviewLine(BaseModel):
    product_id: UUID
    product_label: str
    basis: Suggestion
    unit_id: UUID
    unit_name: str
    unit_to_base_factor: Decimal | None
    conversion_version: int | None
    qty: Decimal | None
    base_qty: Decimal | None
    quantity_source: Literal["SUGGESTION", "MANUAL"]
    quantity_reason: str | None
    price_source: Literal["MANUAL", "HISTORY"]
    unit_price: Decimal | None
    historical_price: HistoricalPrice | None
    amount: Decimal | None
    blocking_reasons: list[str]
    warnings: list[str]


class PurchasePreview(Window):
    supplier_id: UUID
    supplier_name: str
    warehouse_id: UUID
    warehouse_name: str
    reason: str
    lines: list[PreviewLine]
    total_amount: Decimal | None
    can_create: bool
    blocking_reasons: list[str]
    confirmation_hash: str


class ReplenishmentReceipt(PurchaseReceipt):
    creation_id: UUID


class WarehouseOption(BaseModel):
    id: UUID
    code: str
    name: str


class WarehouseOptions(BaseModel):
    items: list[WarehouseOption]
    total: int
    page: int
    page_size: int
