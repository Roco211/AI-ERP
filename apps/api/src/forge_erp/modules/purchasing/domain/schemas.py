from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from forge_erp.modules.catalog.domain.schemas import Amount, InputModel

Reason = Annotated[str, Field(min_length=1, max_length=2000)]
OrderStatus = Literal["DRAFT", "CONFIRMED", "CLOSED", "CANCELLED"]


class PurchaseOrderLineInput(InputModel):
    product_id: UUID
    unit_id: UUID
    qty: Amount
    unit_price: Amount


class PurchaseOrderInput(InputModel):
    supplier_id: UUID
    warehouse_id: UUID
    reason: Reason
    lines: Annotated[list[PurchaseOrderLineInput], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def unique(self):
        if len({x.product_id for x in self.lines}) != len(self.lines):
            raise ValueError("Each product must appear once")
        return self


class PurchaseOrderUpdate(PurchaseOrderInput):
    expected_version: Annotated[int, Field(ge=1)]


class PurchaseVersion(InputModel):
    expected_version: Annotated[int, Field(ge=1)]


class PurchaseAction(PurchaseVersion):
    reason: Reason


class PurchaseDocumentLineInput(InputModel):
    source_line_id: UUID
    qty: Amount
    unit_price: Amount | None = None


class PurchaseDocumentInput(InputModel):
    source_id: UUID
    reason: Reason
    lines: Annotated[list[PurchaseDocumentLineInput], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def unique(self):
        if len({x.source_line_id for x in self.lines}) != len(self.lines):
            raise ValueError("Each source line must appear once")
        return self


class PurchaseDocumentUpdate(PurchaseDocumentInput):
    expected_version: Annotated[int, Field(ge=1)]


class PurchaseReceipt(BaseModel):
    id: UUID
    status: str
    version: int
    request_id: str


class PurchaseOrderLineRead(BaseModel):
    id: UUID
    product_id: UUID
    unit_id: UUID
    product_label: str
    unit_label: str
    qty: Decimal
    unit_to_base_factor: Decimal
    base_qty: Decimal
    conversion_version: int
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    received_base_qty: Decimal
    remaining_qty: Decimal
    remaining_base_qty: Decimal
    returned_base_qty: Decimal


class PurchaseOrderRead(BaseModel):
    id: UUID
    number: str
    status: OrderStatus
    receiving_status: Literal["UNRECEIVED", "PARTIAL", "RECEIVED"]
    version: int
    supplier_id: UUID
    supplier_name: str
    warehouse_id: UUID
    warehouse_name: str
    reason: str
    amount: Decimal | None = None
    created_at: datetime
    action_reason: str | None = None
    lines: list[PurchaseOrderLineRead] = Field(default_factory=list)


class PurchaseOrdersPage(BaseModel):
    items: list[PurchaseOrderRead]
    total: int
    page: int
    page_size: int


class PurchaseDocumentLineRead(BaseModel):
    id: UUID
    product_id: UUID
    unit_id: UUID
    product_label: str
    unit_label: str
    qty: Decimal
    base_qty: Decimal
    unit_to_base_factor: Decimal
    conversion_version: int
    order_line_id: UUID
    receipt_line_id: UUID | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    returnable_qty: Decimal
    inventory_value_delta: Decimal | None = None
    valuation_difference: Decimal | None = None


class PurchaseDocumentRead(BaseModel):
    id: UUID
    number: str
    kind: Literal["RECEIPT", "RETURN"]
    status: Literal["DRAFT", "POSTED", "REVERSED"]
    version: int
    order_id: UUID
    order_number: str
    original_document_id: UUID | None = None
    supplier_id: UUID
    supplier_name: str
    warehouse_id: UUID
    warehouse_name: str
    reason: str
    created_at: datetime
    amount: Decimal | None = None
    lines: list[PurchaseDocumentLineRead] = Field(default_factory=list)


class PurchaseDocumentsPage(BaseModel):
    items: list[PurchaseDocumentRead]
    total: int
    page: int
    page_size: int


class PurchasePriceRead(BaseModel):
    document_id: UUID
    document_number: str
    supplier_id: UUID
    supplier_name: str
    product_id: UUID
    product_label: str
    unit_id: UUID
    unit_label: str
    unit_price: Decimal
    qty: Decimal
    amount: Decimal
    posted_at: datetime


class PurchasePricesPage(BaseModel):
    items: list[PurchasePriceRead]
    total: int
    page: int
    page_size: int
