from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.auth_dependencies import authenticated_snapshot
from forge_erp.core.context import RuntimeContext
from forge_erp.modules.inventory.api.router import Key, Page, Size, Transaction
from forge_erp.modules.replenishment.application import commands, queries
from forge_erp.modules.replenishment.domain import schemas as s

Snapshot = Annotated[
    tuple[AsyncSession, RuntimeContext], Depends(authenticated_snapshot, scope="function")
]
router = APIRouter(prefix="/api/v1/replenishment", tags=["replenishment"])


@router.get("/suggestions", response_model=s.SuggestionsPage)
async def list_suggestions(
    tx: Snapshot,
    page: Page = 1,
    page_size: Size = 25,
    category_id: UUID | None = None,
    supplier_id: UUID | None = None,
    q: str = Query("", max_length=200),
    candidate_only: bool = False,
    suggested_only: bool = False,
):
    return await queries.suggestions(
        *tx, page, page_size, category_id, supplier_id, q, candidate_only, suggested_only
    )


@router.get("/warehouses", response_model=s.WarehouseOptions)
async def warehouse_options(
    tx: Snapshot, page: Page = 1, page_size: Size = 25, q: str = Query("", max_length=200)
):
    return await queries.warehouses(*tx, page, page_size, q)


@router.post("/purchase-preview", response_model=s.PurchasePreview)
async def purchase_preview(body: s.PurchasePreviewInput, tx: Snapshot):
    return await commands.preview(*tx, body)


@router.post("/purchase-orders", response_model=s.ReplenishmentReceipt, status_code=201)
async def create_purchase_order(body: s.PurchaseCreationInput, tx: Transaction, key: Key):
    return await commands.create_purchase(*tx, body, key)
