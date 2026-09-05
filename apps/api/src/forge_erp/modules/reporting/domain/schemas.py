from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

Metric = Literal[
    "sales",
    "cash_ar",
    "cash_ap",
    "current_ar",
    "current_ap",
    "inventory",
    "low_stock",
    "replenishment",
]
IntegrationStatus = Literal["ACTIVE", "INCOMPLETE", "NOT_ENABLED"]


class SalesMetrics(BaseModel):
    shipment_count: int
    return_count: int
    shipment_amount: Decimal | None = None
    return_amount: Decimal | None = None
    net_sales_amount: Decimal | None = None
    cost_status: Literal["AVAILABLE", "MISSING_FACTS"] | None = None
    shipment_cost: Decimal | None = None
    return_cost: Decimal | None = None
    net_cost: Decimal | None = None
    gross_margin: Decimal | None = None


class SalesDay(SalesMetrics):
    date: Date


class PeriodSales(SalesMetrics):
    daily: list[SalesDay]


class CashDay(BaseModel):
    date: Date
    settlement_amount: Decimal
    refund_amount: Decimal
    net_cash_amount: Decimal


class PeriodCash(BaseModel):
    integration_status: IntegrationStatus
    unmapped_document_count: int
    settlement_amount: Decimal | None = None
    refund_amount: Decimal | None = None
    net_cash_amount: Decimal | None = None
    daily: list[CashDay]


class CurrentFunds(BaseModel):
    integration_status: IntegrationStatus
    unmapped_document_count: int
    source_count: int | None = None
    balance: Decimal | None = None
    settlement_amount: Decimal | None = None
    refund_amount: Decimal | None = None


class CurrentInventory(BaseModel):
    product_count: int
    low_stock_count: int
    valuation: Decimal | None = None


class CurrentReplenishment(BaseModel):
    candidate_count: int
    suggested_count: int


class ReportScope(BaseModel):
    as_of: datetime
    business_today: Date
    business_timezone: str
    date_from: Date
    date_to: Date
    restatement_notice: str


class OperatingOverview(ReportScope):
    sales: PeriodSales | None = None
    cash_ar: PeriodCash | None = None
    cash_ap: PeriodCash | None = None
    current_ar: CurrentFunds | None = None
    current_ap: CurrentFunds | None = None
    inventory: CurrentInventory | None = None
    replenishment: CurrentReplenishment | None = None


class ReportSource(BaseModel):
    id: UUID
    number: str
    kind: str
    label: str
    document_id: UUID | None = None
    order_id: UUID | None = None
    product_id: UUID | None = None
    party_name: str | None = None
    date: Date | None = None
    unit_name: str | None = None
    warehouse_name: str | None = None
    qty: Decimal | None = None
    amount: Decimal | None = None
    signed_amount: Decimal | None = None
    actual_cost: Decimal | None = None
    signed_cost: Decimal | None = None
    gross_margin: Decimal | None = None
    cost_status: Literal["AVAILABLE", "MISSING_FACTS"] | None = None
    balance: Decimal | None = None
    settlement_amount: Decimal | None = None
    refund_amount: Decimal | None = None
    valuation: Decimal | None = None
    available_qty: Decimal | None = None
    reserved_qty: Decimal | None = None
    min_stock_qty: Decimal | None = None
    suggested_base_qty: Decimal | None = None


class ReportSources(ReportScope):
    metric: Metric
    items: list[ReportSource]
    total: int
    page: int
    page_size: int
    integration_status: IntegrationStatus | None = None
    unmapped_document_count: int | None = None
