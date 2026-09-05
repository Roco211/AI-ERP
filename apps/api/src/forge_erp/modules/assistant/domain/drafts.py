"""Strict assistant inputs wrap, rather than replace, the ERP order contracts."""

from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from forge_erp.modules.catalog.domain.schemas import InputModel
from forge_erp.modules.purchasing.domain.schemas import PurchaseOrderInput
from forge_erp.modules.sales.domain.schemas import SalesOrderInput


class _DraftInput[OrderInput: (SalesOrderInput, PurchaseOrderInput)](InputModel):
    order: OrderInput

    @model_validator(mode="before")
    @classmethod
    def decimal_strings(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("order"), dict):
            lines = value["order"].get("lines")
            if isinstance(lines, list):
                if len(lines) > 20:
                    raise ValueError("助手草稿最多20行，请拆分开单")
                for line in lines:
                    if isinstance(line, dict):
                        for field in ("qty", "unit_price"):
                            number = line.get(field)
                            if number is not None and not isinstance(number, str):
                                raise ValueError("数量和价格必须使用十进制字符串")
        return value

    @model_validator(mode="after")
    def bounded_order(self) -> _DraftInput:
        order = self.order
        if len(order.lines) > 20:
            raise ValueError("助手草稿最多20行，请拆分开单")
        if any(line.qty <= 0 for line in order.lines):
            raise ValueError("草稿数量必须大于零")
        return self


class SalesDraftInput(_DraftInput[SalesOrderInput]):
    kind: Literal["SALES"]


class PurchaseDraftInput(_DraftInput[PurchaseOrderInput]):
    kind: Literal["PURCHASE"]


DraftInput = Annotated[SalesDraftInput | PurchaseDraftInput, Field(discriminator="kind")]
DRAFT_INPUT = TypeAdapter(DraftInput)


class DraftLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: UUID
    unit_id: UUID
    product_label: str
    unit_label: str
    qty: Decimal
    unit_price: Decimal
    amount: Decimal
    unit_to_base_factor: Decimal
    base_qty: Decimal
    conversion_version: int
    pricing_mode: Literal["AUTO", "MANUAL"]
    price_source: dict[str, Any]


class DraftPreview(BaseModel):
    kind: Literal["SALES", "PURCHASE"]
    order: SalesOrderInput | PurchaseOrderInput
    party_name: str
    warehouse_name: str
    lines: list[DraftLine]
    total_amount: Decimal
    confirmation_hash: str
    warnings: list[str]
