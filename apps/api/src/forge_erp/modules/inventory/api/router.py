from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.auth_dependencies import authenticated_transaction
from forge_erp.core.context import RuntimeContext
from forge_erp.modules.inventory.application import documents as commands
from forge_erp.modules.inventory.application import queries
from forge_erp.modules.inventory.domain import schemas as s

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])
Transaction = Annotated[tuple[AsyncSession, RuntimeContext], Depends(authenticated_transaction)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
Page = Annotated[int, Query(ge=1)]
Size = Annotated[int, Query(ge=1, le=100)]


@router.get("/balances", response_model=s.InventoryBalancesPage, response_model_exclude_none=True)
async def list_inventory_balances(
    tx: Transaction,
    warehouse_id: UUID | None = None,
    product_id: UUID | None = None,
    q: str = Query("", max_length=200),
    page: Page = 1,
    page_size: Size = 25,
):
    return await queries.balances(*tx, warehouse_id, product_id, q, page, page_size)


@router.get("/movements", response_model=s.InventoryMovementsPage, response_model_exclude_none=True)
async def list_inventory_movements(
    tx: Transaction,
    warehouse_id: UUID | None = None,
    product_id: UUID | None = None,
    document_id: UUID | None = None,
    page_size: Size = 25,
    cursor: str | None = Query(None, max_length=4096),
):
    return await queries.movements(*tx, warehouse_id, product_id, document_id, page_size, cursor)


@router.get("/low-stock", response_model=s.LowStockPage)
async def list_low_stock(tx: Transaction, page: Page = 1, page_size: Size = 25):
    return await queries.low_stock(*tx, page, page_size)


@router.get("/documents", response_model=s.InventoryDocumentsPage, response_model_exclude_none=True)
async def list_inventory_documents(
    tx: Transaction, page: Page = 1, page_size: Size = 25, kind: s.Kind | None = None
):
    return await queries.documents(*tx, page, page_size, kind=kind)


@router.get(
    "/documents/{id}", response_model=s.InventoryDocumentRead, response_model_exclude_none=True
)
async def get_inventory_document(id: UUID, tx: Transaction):
    return await queries.documents(*tx, 1, 1, id=id)


@router.post("/openings", response_model=s.InventoryReceipt, status_code=201)
async def create_opening(body: s.OpeningInput, tx: Transaction, key: Key):
    return await commands.save_draft(*tx, "OPENING", body, key)


@router.post("/adjustments", response_model=s.InventoryReceipt, status_code=201)
async def create_adjustment(body: s.AdjustmentInput, tx: Transaction, key: Key):
    return await commands.save_draft(*tx, "ADJUSTMENT", body, key)


@router.post("/transfers", response_model=s.InventoryReceipt, status_code=201)
async def create_transfer(body: s.TransferInput, tx: Transaction, key: Key):
    return await commands.save_draft(*tx, "TRANSFER", body, key)


@router.post("/stocktakes", response_model=s.InventoryReceipt, status_code=201)
async def create_stocktake(body: s.StocktakeInput, tx: Transaction, key: Key):
    return await commands.save_draft(*tx, "STOCKTAKE", body, key)


@router.put("/documents/{id}/draft", response_model=s.InventoryReceipt)
async def update_inventory_draft(id: UUID, body: s.InventoryDraftUpdate, tx: Transaction, key: Key):
    doc = await commands.load(*tx, id)
    return await commands.save_draft(*tx, doc["type"], body, key, id, body.expected_version)


@router.post("/documents/{id}/post", response_model=s.InventoryReceipt)
async def post_inventory_document(id: UUID, body: s.InventoryVersion, tx: Transaction, key: Key):
    return await commands.transition(*tx, id, body.expected_version, key, "post")


@router.post("/documents/{id}/reverse", response_model=s.InventoryReceipt)
async def reverse_inventory_document(id: UUID, body: s.InventoryReverse, tx: Transaction, key: Key):
    return await commands.transition(*tx, id, body.expected_version, key, "reverse", body.reason)


@router.post("/stocktakes/{id}/refresh-baseline", response_model=s.InventoryReceipt)
async def refresh_stocktake(id: UUID, body: s.InventoryVersion, tx: Transaction, key: Key):
    return await commands.transition(*tx, id, body.expected_version, key, "refresh")
