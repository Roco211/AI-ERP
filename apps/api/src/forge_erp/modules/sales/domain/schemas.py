from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from forge_erp.modules.catalog.domain.schemas import Amount, Factor, InputModel
from forge_erp.modules.funds.domain.schemas import FundsOrderSummary

Reason = Annotated[str, Field(min_length=1, max_length=2000)]
OrderStatus = Literal["DRAFT", "CONFIRMED", "CLOSED", "CANCELLED"]
DocumentStatus = Literal["DRAFT", "POSTED", "REVERSED"]
DocumentKind = Literal["SHIPMENT", "RETURN"]


class SalesOrderLineInput(InputModel):
    product_id: UUID
    unit_id: UUID
    qty: Factor
    pricing_mode: Literal["AUTO", "MANUAL"] = "AUTO"
    unit_price: Amount | None = None

    @model_validator(mode="after")
    def price_input(self):
        if (self.pricing_mode == "MANUAL") != (self.unit_price is not None):
            raise ValueError("MANUAL requires an explicit price; AUTO accepts no client price")
        return self


class SalesOrderInput(InputModel):
    customer_id: UUID
    warehouse_id: UUID
    reason: Reason
    lines: Annotated[list[SalesOrderLineInput], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def unique(self):
        if len({x.product_id for x in self.lines}) != len(self.lines):
            raise ValueError("Each product must appear once")
        return self


class SalesOrderUpdate(SalesOrderInput):
    expected_version: Annotated[int, Field(ge=1)]


class SalesVersion(InputModel):
    expected_version: Annotated[int, Field(ge=1)]


class SalesAction(SalesVersion):
    reason: Reason


class SalesReceipt(BaseModel):
    id: UUID
    status: OrderStatus
    version: int
    request_id: str


class PriceSource(BaseModel):
    source: Literal["customer", "history", "retail", "wholesale", "standard", "manual", "unset"]
    source_id: UUID | None = None
    source_document_id: UUID | None = None
    original_unit_id: UUID | None = None
    original_factor: Decimal | None = None
    original_price: Decimal | None = None
    target_unit_id: UUID
    target_factor: Decimal
    rounding: str = "ROUND_HALF_UP; final unit price 6 decimals"


class PriceQuote(BaseModel):
    product_id: UUID
    customer_id: UUID
    unit_price: Decimal | None = None
    price_source: PriceSource


class SalesOrderLineRead(BaseModel):
    id: UUID
    product_id: UUID
    unit_id: UUID
    product_label: str
    unit_label: str
    qty: Decimal
    unit_to_base_factor: Decimal
    base_qty: Decimal
    conversion_version: int
    pricing_mode: Literal["AUTO", "MANUAL"] | None = None
    price_source: PriceSource | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    shipped_base_qty: Decimal
    remaining_base_qty: Decimal
    returned_base_qty: Decimal
    reserved_base_qty: Decimal
    executable_base_qty: Decimal
    remaining_qty: Decimal = Field(description="Unshipped quantity in the frozen order unit")
    executable_qty: Decimal = Field(description="Executable quantity in the frozen order unit")
    on_hand_qty: Decimal | None = Field(
        default=None, description="Live warehouse stock in base units; requires inventory.read"
    )
    warehouse_reserved_qty: Decimal | None = Field(
        default=None,
        description="Live warehouse total reserved base units; requires inventory.read",
    )
    available_qty: Decimal | None = Field(
        default=None, description="Live warehouse available base units; requires inventory.read"
    )


class SalesOrderRead(BaseModel):
    id: UUID
    number: str
    status: OrderStatus
    fulfillment_status: Literal["UNFULFILLED", "PARTIAL", "FULFILLED"]
    version: int
    customer_id: UUID
    customer_name: str
    warehouse_id: UUID
    warehouse_name: str
    reason: str
    amount: Decimal | None = None
    created_at: datetime
    action_reason: str | None = None
    shipment_amount: Decimal | None = None
    shipment_cost: Decimal | None = None
    return_amount: Decimal | None = None
    return_cost: Decimal | None = None
    net_sales_amount: Decimal | None = None
    net_cost: Decimal | None = None
    gross_margin: Decimal | None = None
    funds: FundsOrderSummary | None = None
    lines: list[SalesOrderLineRead] = Field(default_factory=list)


class SalesOrdersPage(BaseModel):
    items: list[SalesOrderRead]
    total: int
    page: int
    page_size: int


class SalesShipmentLineInput(InputModel):
    source_line_id: UUID
    qty: Factor


class SalesShipmentInput(InputModel):
    source_id: UUID
    reason: Reason
    lines: Annotated[list[SalesShipmentLineInput], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def unique(self):
        if len({x.source_line_id for x in self.lines}) != len(self.lines):
            raise ValueError("Each source line must appear once")
        return self


class SalesReturnInput(SalesShipmentInput):
    pass


class SalesShipmentUpdate(SalesShipmentInput):
    expected_version: Annotated[int, Field(ge=1)]


class SalesDocumentReceipt(BaseModel):
    id: UUID
    status: Literal["DRAFT", "POSTED", "REVERSED"]
    version: int
    request_id: str


class SalesDocumentLineRead(BaseModel):
    id: UUID
    order_line_id: UUID
    reservation_source_line_id: UUID | None = None
    shipment_line_id: UUID | None = None
    original_line_id: UUID | None = None
    product_id: UUID
    unit_id: UUID
    product_label: str
    unit_label: str
    qty: Decimal
    base_qty: Decimal
    unit_to_base_factor: Decimal
    conversion_version: int
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    actual_cost: Decimal | None = None
    return_cost: Decimal | None = None
    returned_qty: Decimal = Decimal(0)
    returned_base_qty: Decimal = Decimal(0)
    returned_amount: Decimal | None = None
    returned_cost: Decimal | None = None
    returnable_qty: Decimal | None = None
    returnable_base_qty: Decimal | None = None
    gross_margin: Decimal | None = None


class SalesDocumentRead(BaseModel):
    id: UUID
    number: str
    kind: DocumentKind
    original_document_id: UUID | None = None
    status: Literal["DRAFT", "POSTED", "REVERSED"]
    version: int
    order_id: UUID
    order_number: str
    customer_id: UUID
    customer_name: str
    warehouse_id: UUID
    warehouse_name: str
    reason: str
    created_at: datetime
    posted_at: datetime | None = None
    amount: Decimal | None = None
    actual_cost: Decimal | None = None
    gross_margin: Decimal | None = None
    lines: list[SalesDocumentLineRead] = Field(default_factory=list)


class SalesDocumentsPage(BaseModel):
    items: list[SalesDocumentRead]
    total: int
    page: int
    page_size: int


class SalesPriceHistoryRead(BaseModel):
    line_id: UUID
    document_id: UUID
    document_number: str
    order_id: UUID
    customer_id: UUID
    customer_name: str
    product_id: UUID
    product_label: str
    unit_id: UUID
    unit_label: str
    unit_price: Decimal
    qty: Decimal
    base_qty: Decimal
    unit_to_base_factor: Decimal
    conversion_version: int
    amount: Decimal
    posted_at: datetime
    returned_qty: Decimal
    returned_base_qty: Decimal
    returned_amount: Decimal


class SalesPriceHistoryPage(BaseModel):
    items: list[SalesPriceHistoryRead]
    total: int
    page: int
    page_size: int
