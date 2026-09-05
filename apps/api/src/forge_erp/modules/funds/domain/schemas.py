from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, Field, model_validator

from forge_erp.modules.catalog.domain.schemas import InputModel, reject_float

Side = Literal["AR", "AP"]
CashKind = Literal["SETTLEMENT", "REFUND"]
Method = Literal["CASH", "BANK_TRANSFER", "OTHER"]
SourceKind = Literal["SHIPMENT", "RECEIPT", "OPENING", "LEGACY", "ADJUSTMENT"]
SourceState = Literal["OPEN", "REFUND", "SETTLED", "REVERSED"]
Money = Annotated[
    Decimal,
    BeforeValidator(reject_float),
    Field(
        gt=Decimal("-1e16"),
        lt=Decimal("1e16"),
        max_digits=20,
        decimal_places=4,
        allow_inf_nan=False,
    ),
]
Reason = Annotated[str, Field(min_length=1, max_length=2000)]


class FundsReason(InputModel):
    reason: Reason


class FundsActivate(FundsReason):
    business_date: date


class FundsOpening(FundsReason):
    side: Side
    party_id: UUID
    amount: Money
    refund_balance_confirmed: bool = False


class FundsLegacyBinding(FundsReason):
    side: Side
    source_document_id: UUID
    opening_source_id: UUID | None = None
    amount: Money
    refund_balance_confirmed: bool = False


class FundsAllocationInput(InputModel):
    source_id: UUID
    amount: Money


class FundsCashInput(FundsReason):
    side: Side
    party_id: UUID
    kind: CashKind
    business_date: date
    method: Method
    external_reference: Annotated[str, Field(max_length=200)] = ""
    allocations: Annotated[list[FundsAllocationInput], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def unique_sources(self):
        if len({line.source_id for line in self.allocations}) != len(self.allocations):
            raise ValueError("Each source must appear once")
        return self


class FundsReceipt(BaseModel):
    id: UUID
    status: Literal["POSTED", "REVERSED", "ENABLED"]
    request_id: str


class FundsPreviewAllocation(BaseModel):
    source_id: UUID
    source_number: str
    amount: Decimal
    available_amount: Decimal


class FundsCashPreview(BaseModel):
    side: Side
    party_id: UUID
    party_name: str
    kind: CashKind
    amount: Decimal
    allocations: list[FundsPreviewAllocation]


class FundsSettings(BaseModel):
    enabled: bool
    business_timezone: str
    business_today: date
    business_date: date | None = None
    activated_at: datetime | None = None
    reason: str | None = None
    currency: Literal["CNY"] = "CNY"


class FundsSummary(BaseModel):
    side: Side
    currency: Literal["CNY"] = "CNY"
    balance: Decimal
    settlement_amount: Decimal
    refund_amount: Decimal
    source_amount: Decimal
    settled_amount: Decimal
    refunded_amount: Decimal
    party_count: int


class FundsParty(BaseModel):
    id: UUID
    code: str
    name: str
    is_active: bool
    balance: Decimal
    settlement_amount: Decimal
    refund_amount: Decimal


class FundsPartiesPage(BaseModel):
    items: list[FundsParty]
    page: int
    page_size: int
    total: int


class FundsSource(BaseModel):
    id: UUID
    number: str
    side: Side
    party_id: UUID
    party_name: str
    kind: SourceKind
    status: SourceState
    settlement_status: Literal["UNPAID", "PARTIAL", "PAID"]
    source_document_id: UUID | None = None
    source_document_number: str | None = None
    business_date: date
    created_at: datetime
    amount: Decimal
    commercial_amount: Decimal
    settled_amount: Decimal
    historically_settled_amount: Decimal = Decimal("0.0000")
    refunded_amount: Decimal
    balance: Decimal
    settlement_amount: Decimal
    refund_amount: Decimal
    reason: str


class FundsOrderSummary(BaseModel):
    integration_status: Literal["NOT_ENABLED", "INCOMPLETE", "ACTIVE"]
    unmapped_document_count: int
    settlement_status: Literal["UNPAID", "PARTIAL", "PAID"] | None = None
    historically_settled_amount: Decimal = Decimal("0.0000")
    source_amount: Decimal
    settled_amount: Decimal
    refunded_amount: Decimal
    balance: Decimal
    settlement_amount: Decimal
    refund_amount: Decimal


class FundsEntry(BaseModel):
    id: UUID
    kind: str
    amount: Decimal
    document_id: UUID | None = None
    related_source_id: UUID | None = None
    reason: str
    created_at: datetime


class FundsSourceDetail(FundsSource):
    entries: list[FundsEntry]
    cash: list[FundsCashAllocationRead]


class FundsSourcesPage(BaseModel):
    items: list[FundsSource]
    page: int
    page_size: int
    total: int


class FundsCashAllocationRead(BaseModel):
    cash_id: UUID
    cash_number: str
    cash_kind: CashKind
    cash_status: Literal["POSTED", "REVERSED"]
    source_id: UUID
    source_number: str
    amount: Decimal


class FundsCash(BaseModel):
    id: UUID
    number: str
    side: Side
    party_id: UUID
    party_name: str
    kind: CashKind
    status: Literal["POSTED", "REVERSED"]
    amount: Decimal
    business_date: date
    method: Method
    external_reference: str
    reason: str
    created_at: datetime
    reversed_at: datetime | None = None
    reversal_reason: str | None = None


class FundsCashDetail(FundsCash):
    allocations: list[FundsCashAllocationRead]


class FundsCashPage(BaseModel):
    items: list[FundsCash]
    page: int
    page_size: int
    total: int


class FundsLegacyDocument(BaseModel):
    id: UUID
    number: str
    side: Side
    party_id: UUID
    party_name: str
    posted_at: datetime
    commercial_amount: Decimal
    returned_amount: Decimal
    effective_amount: Decimal


class FundsLegacyPage(BaseModel):
    items: list[FundsLegacyDocument]
    page: int
    page_size: int
    total: int
