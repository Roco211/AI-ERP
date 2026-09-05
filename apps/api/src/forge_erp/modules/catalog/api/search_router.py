from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from forge_erp.modules.catalog.api.router import Transaction
from forge_erp.modules.catalog.application.search import conversion, resolve_price, search_products
from forge_erp.modules.catalog.domain.schemas import Amount, InputModel, Page, ProductRead
from forge_erp.modules.catalog.domain.values import ConversionSnapshot

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])


class PriceRead(InputModel):
    product_id: UUID
    base_unit_id: UUID
    price: Amount | None
    source: str


@router.get("/search", response_model=Page[ProductRead], operation_id="searchProducts")
async def search(
    tx: Transaction,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    category_id: UUID | None = None,
    brand_id: UUID | None = None,
    attribute_key: str | None = Query(default=None, max_length=40),
    attribute_value: str | None = Query(default=None, max_length=300),
):
    attributes = (
        {attribute_key: attribute_value} if attribute_key and attribute_value is not None else None
    )
    return await search_products(
        *tx,
        q,
        page,
        page_size,
        True,
        {"category_id": category_id, "brand_id": brand_id},
        attributes,
    )


@router.get("/conversion", response_model=ConversionSnapshot, operation_id="previewConversion")
async def preview_conversion(
    tx: Transaction, product_id: UUID, unit_id: UUID, qty: Annotated[Amount, Query()]
):
    return await conversion(*tx, product_id, unit_id, qty)


@router.get("/price", response_model=PriceRead, operation_id="resolveProductPrice")
async def price(tx: Transaction, product_id: UUID, customer_id: UUID | None = None):
    return await resolve_price(*tx, product_id, customer_id)
