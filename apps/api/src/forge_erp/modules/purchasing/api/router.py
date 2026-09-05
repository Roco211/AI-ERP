from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query

from forge_erp.modules.inventory.api.router import Key, Page, Size, Transaction
from forge_erp.modules.purchasing.application import documents, orders, queries
from forge_erp.modules.purchasing.domain import schemas as s

router = APIRouter(prefix="/api/v1/purchasing", tags=["purchasing"])


@router.get("/orders", response_model=s.PurchaseOrdersPage, response_model_exclude_none=True)
async def list_purchase_orders(
    tx: Transaction,
    page: Page = 1,
    page_size: Size = 25,
    supplier_id: UUID | None = None,
    status: s.OrderStatus | None = None,
    q: str = Query("", max_length=200),
):
    return await queries.order_list(*tx, page, page_size, supplier_id, status, q)


@router.get("/orders/{id}", response_model=s.PurchaseOrderRead, response_model_exclude_none=True)
async def get_purchase_order(id: UUID, tx: Transaction):
    return await queries.order_detail(*tx, id)


@router.post("/orders", response_model=s.PurchaseReceipt, status_code=201)
async def create_purchase_order(body: s.PurchaseOrderInput, tx: Transaction, key: Key):
    return await orders.save(*tx, body, key)


@router.put("/orders/{id}/draft", response_model=s.PurchaseReceipt)
async def update_purchase_order(id: UUID, body: s.PurchaseOrderUpdate, tx: Transaction, key: Key):
    return await orders.save(*tx, body, key, id, body.expected_version)


@router.post("/orders/{id}/confirm", response_model=s.PurchaseReceipt)
async def confirm_purchase_order(id: UUID, body: s.PurchaseVersion, tx: Transaction, key: Key):
    return await orders.transition(*tx, id, body.expected_version, key, "confirm")


@router.post("/orders/{id}/cancel", response_model=s.PurchaseReceipt)
async def cancel_purchase_order(id: UUID, body: s.PurchaseAction, tx: Transaction, key: Key):
    return await orders.transition(*tx, id, body.expected_version, key, "cancel", body.reason)


@router.post("/orders/{id}/close", response_model=s.PurchaseReceipt)
async def close_purchase_order(id: UUID, body: s.PurchaseAction, tx: Transaction, key: Key):
    return await orders.transition(*tx, id, body.expected_version, key, "close", body.reason)


@router.get("/documents", response_model=s.PurchaseDocumentsPage, response_model_exclude_none=True)
async def list_purchase_documents(
    tx: Transaction,
    page: Page = 1,
    page_size: Size = 25,
    kind: Literal["RECEIPT", "RETURN"] | None = None,
    order_id: UUID | None = None,
):
    return await queries.document_list(*tx, page, page_size, kind, order_id)


@router.get(
    "/documents/{id}", response_model=s.PurchaseDocumentRead, response_model_exclude_none=True
)
async def get_purchase_document(id: UUID, tx: Transaction):
    return await queries.document_detail(*tx, id)


@router.get("/price-history", response_model=s.PurchasePricesPage)
async def get_purchase_prices(
    tx: Transaction,
    page: Page = 1,
    page_size: Size = 25,
    supplier_id: UUID | None = None,
    product_id: UUID | None = None,
):
    return await queries.price_history(*tx, page, page_size, supplier_id, product_id)


@router.post("/receipts", response_model=s.PurchaseReceipt, status_code=201)
async def create_purchase_receipt(body: s.PurchaseDocumentInput, tx: Transaction, key: Key):
    return await documents.save(*tx, "RECEIPT", body, key)


@router.post("/returns", response_model=s.PurchaseReceipt, status_code=201)
async def create_purchase_return(body: s.PurchaseDocumentInput, tx: Transaction, key: Key):
    return await documents.save(*tx, "RETURN", body, key)


@router.put("/documents/{id}/draft", response_model=s.PurchaseReceipt)
async def update_purchase_document(
    id: UUID, body: s.PurchaseDocumentUpdate, tx: Transaction, key: Key
):
    current = await queries.raw_document(*tx, id)
    return await documents.save(*tx, current["kind"], body, key, id, body.expected_version)


@router.post("/documents/{id}/post", response_model=s.PurchaseReceipt)
async def post_purchase_document(id: UUID, body: s.PurchaseVersion, tx: Transaction, key: Key):
    return await documents.transition(*tx, id, body.expected_version, key, "post")


@router.post("/documents/{id}/reverse", response_model=s.PurchaseReceipt)
async def reverse_purchase_document(id: UUID, body: s.PurchaseAction, tx: Transaction, key: Key):
    return await documents.transition(*tx, id, body.expected_version, key, "reverse", body.reason)
