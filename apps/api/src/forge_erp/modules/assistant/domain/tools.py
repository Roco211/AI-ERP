"""Bounded query inputs and server-authored evidence shared with the assistant UI."""

from datetime import date, datetime
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, field_validator, model_validator

from forge_erp.modules.catalog.domain.schemas import InputModel
from forge_erp.modules.reporting.domain.schemas import Metric


class Fact(BaseModel):
    label: str
    value: str
    unit: str | None = None


class SourceLink(BaseModel):
    label: str
    href: str

    @field_validator("href")
    @classmethod
    def internal_source(cls, value: str) -> str:
        allowed = {
            "/products": set(),
            "/customers": set(),
            "/suppliers": set(),
            "/settings/units": set(),
            "/settings/warehouses": set(),
            "/inventory": {"document", "tab"},
            "/sales": {"order", "document"},
            "/purchase": {"order", "document"},
            "/funds": {"source", "side"},
            "/dashboard": set(),
            "/replenishment": set(),
        }
        parsed = urlsplit(value)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or parsed.path not in allowed
            or not value.startswith("/")
            or "\\" in value
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("Only server-approved internal source links are allowed")
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if len(pairs) != len(dict(pairs)):
            raise ValueError("Duplicate source parameters")
        for key, item in pairs:
            if key not in allowed[parsed.path]:
                raise ValueError("Unknown source parameter")
            if key == "side":
                if item not in {"AR", "AP"}:
                    raise ValueError("Unknown funds side")
            elif key == "tab":
                if item != "movements":
                    raise ValueError("Unknown inventory tab")
            else:
                UUID(item)
        return value


class Evidence(BaseModel):
    id: str
    tool: str
    title: str
    as_of: datetime
    scope: str
    summary: list[Fact]
    columns: list[str]
    rows: list[dict[str, str | None]]
    links: list[SourceLink]
    truncated: bool


class ToolResult(BaseModel):
    payload: dict[str, JsonValue]
    evidence: Evidence


class EmptyInput(InputModel):
    pass


class PageInput(InputModel):
    page: Annotated[int, Field(strict=True, ge=1, le=1000)] = 1
    page_size: Annotated[int, Field(strict=True, ge=1, le=25)] = 10


class SearchInput(PageInput):
    q: Annotated[str, Field(strict=True, max_length=200)] = ""


class ProductSearchInput(SearchInput):
    category_id: UUID | None = None
    brand_id: UUID | None = None
    attribute_key: Annotated[str, Field(min_length=1, max_length=40)] | None = None
    attribute_value: Annotated[str, Field(max_length=300)] | None = None

    @model_validator(mode="after")
    def attribute_pair(self):
        if (self.attribute_key is None) != (self.attribute_value is None):
            raise ValueError("Attribute key and value must be supplied together")
        return self


class ProductInput(InputModel):
    product_id: UUID


class ProductUnitsInput(PageInput):
    product_id: UUID


class ConversionInput(ProductInput):
    unit_id: UUID
    qty: Annotated[
        str,
        Field(strict=True, pattern=r"^(?:0|[1-9][0-9]{0,13})(?:\.[0-9]{1,6})?$"),
    ]


class InventoryInput(SearchInput):
    warehouse_id: UUID | None = None
    product_id: UUID | None = None


class MovementsInput(InputModel):
    warehouse_id: UUID | None = None
    product_id: UUID | None = None
    document_id: UUID | None = None
    page_size: Annotated[int, Field(strict=True, ge=1, le=25)] = 10
    cursor: Annotated[str, Field(max_length=4096)] | None = None


class ReportInput(InputModel):
    date_from: date | None = None
    date_to: date | None = None


class ReportSourcesInput(ReportInput, PageInput):
    metric: Metric


class FundsSourcesInput(SearchInput):
    side: Literal["AR", "AP"]
    party_id: UUID | None = None
    status: Literal["OPEN", "REFUND"] = "OPEN"


class ReplenishmentInput(SearchInput):
    category_id: UUID | None = None
    supplier_id: UUID | None = None
    candidate_only: Annotated[bool, Field(strict=True)] = True
    suggested_only: Annotated[bool, Field(strict=True)] = False


class SalesQuoteInput(ProductInput):
    customer_id: UUID
    unit_id: UUID


class SalesHistoryInput(PageInput):
    customer_id: UUID
    product_id: UUID | None = None


class PurchaseHistoryInput(PageInput):
    supplier_id: UUID
    product_id: UUID | None = None
