from uuid import UUID

from fastapi import APIRouter, Query

from forge_erp.modules.inventory.api.router import Key, Page, Size, Transaction
from forge_erp.modules.sales.application import orders, pricing, queries
from forge_erp.modules.sales.domain import schemas as s

router = APIRouter(prefix="/api/v1/sales", tags=["sales"])


@router.get("/orders", response_model=s.SalesOrdersPage, response_model_exclude_none=True)
async def list_sales_orders(
    tx: Transaction,
    page: Page = 1,
    page_size: Size = 25,
    customer_id: UUID | None = None,
    status: s.OrderStatus | None = None,
    q: str = Query("", max_length=200),
):
    return await queries.order_list(*tx, page, page_size, customer_id, status, q)


@router.get("/orders/{id}", response_model=s.SalesOrderRead, response_model_exclude_none=True)
async def get_sales_order(id: UUID, tx: Transaction):
    return await queries.order_detail(*tx, id)


@router.post("/orders", response_model=s.SalesReceipt, status_code=201)
async def create_sales_order(body: s.SalesOrderInput, tx: Transaction, key: Key):
    return await orders.save(*tx, body, key)


@router.put("/orders/{id}/draft", response_model=s.SalesReceipt)
async def update_sales_order(id: UUID, body: s.SalesOrderUpdate, tx: Transaction, key: Key):
    return await orders.save(*tx, body, key, id, body.expected_version)


@router.post("/orders/{id}/confirm", response_model=s.SalesReceipt)
async def confirm_sales_order(id: UUID, body: s.SalesVersion, tx: Transaction, key: Key):
    return await orders.transition(*tx, id, body.expected_version, key, "confirm")


@router.post("/orders/{id}/cancel", response_model=s.SalesReceipt)
async def cancel_sales_order(id: UUID, body: s.SalesAction, tx: Transaction, key: Key):
    return await orders.transition(*tx, id, body.expected_version, key, "cancel", body.reason)


@router.post("/orders/{id}/close", response_model=s.SalesReceipt)
async def close_sales_order(id: UUID, body: s.SalesAction, tx: Transaction, key: Key):
    return await orders.transition(*tx, id, body.expected_version, key, "close", body.reason)


@router.get("/price-quote", response_model=s.PriceQuote, response_model_exclude_none=True)
async def get_sales_quote(customer_id: UUID, product_id: UUID, unit_id: UUID, tx: Transaction):
    return await pricing.quote(*tx, customer_id, product_id, unit_id)
