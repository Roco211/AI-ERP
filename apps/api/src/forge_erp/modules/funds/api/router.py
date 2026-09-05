from uuid import UUID

from fastapi import APIRouter, Query

from forge_erp.modules.funds.application import commands, queries
from forge_erp.modules.funds.domain import schemas as s
from forge_erp.modules.inventory.api.router import Key, Page, Size, Transaction

router = APIRouter(prefix="/api/v1/funds", tags=["funds"])


@router.get("/settings", response_model=s.FundsSettings)
async def get_funds_settings(tx: Transaction):
    return await queries.settings(*tx)


@router.post("/activate", response_model=s.FundsReceipt, status_code=201)
async def activate_funds(body: s.FundsActivate, tx: Transaction, key: Key):
    return await commands.activate(*tx, body, key)


@router.get("/summary", response_model=s.FundsSummary)
async def funds_summary(side: s.Side, tx: Transaction, party_id: UUID | None = None):
    return await queries.summary(*tx, side, party_id)


@router.get("/parties", response_model=s.FundsPartiesPage)
async def funds_parties(
    side: s.Side,
    tx: Transaction,
    page: Page = 1,
    page_size: Size = 25,
    q: str = Query("", max_length=200),
):
    return await queries.parties(*tx, side, page, page_size, q)


@router.get("/sources", response_model=s.FundsSourcesPage)
async def funds_sources(
    side: s.Side,
    tx: Transaction,
    page: Page = 1,
    page_size: Size = 25,
    party_id: UUID | None = None,
    status: s.SourceState | None = None,
    q: str = Query("", max_length=200),
):
    return await queries.sources(*tx, side, page, page_size, party_id, status, q)


@router.get("/sources/{id}", response_model=s.FundsSourceDetail)
async def funds_source_detail(id: UUID, tx: Transaction):
    return await queries.source_detail(*tx, id)


@router.post("/openings", response_model=s.FundsReceipt, status_code=201)
async def create_funds_opening(body: s.FundsOpening, tx: Transaction, key: Key):
    return await commands.opening(*tx, body, key)


@router.post("/adjustments", response_model=s.FundsReceipt, status_code=201)
async def create_funds_adjustment(body: s.FundsOpening, tx: Transaction, key: Key):
    return await commands.opening(*tx, body, key, adjustment=True)


@router.post("/sources/{id}/reverse", response_model=s.FundsReceipt)
async def reverse_funds_source(id: UUID, body: s.FundsReason, tx: Transaction, key: Key):
    return await commands.reverse_source(*tx, id, body.reason, key)


@router.get("/legacy-documents", response_model=s.FundsLegacyPage)
async def funds_legacy_documents(
    side: s.Side,
    tx: Transaction,
    page: Page = 1,
    page_size: Size = 25,
    party_id: UUID | None = None,
):
    return await queries.legacy_documents(*tx, side, page, page_size, party_id)


@router.post("/legacy-bindings", response_model=s.FundsReceipt, status_code=201)
async def bind_funds_legacy(body: s.FundsLegacyBinding, tx: Transaction, key: Key):
    return await commands.bind_legacy(*tx, body, key)


@router.get("/cash", response_model=s.FundsCashPage)
async def funds_cash(
    side: s.Side,
    tx: Transaction,
    page: Page = 1,
    page_size: Size = 25,
    party_id: UUID | None = None,
    kind: s.CashKind | None = None,
):
    return await queries.cash_list(*tx, side, page, page_size, party_id, kind)


@router.get("/cash/{id}", response_model=s.FundsCashDetail)
async def funds_cash_detail(id: UUID, tx: Transaction):
    return await queries.cash_detail(*tx, id)


@router.post("/cash", response_model=s.FundsReceipt, status_code=201)
async def create_funds_cash(body: s.FundsCashInput, tx: Transaction, key: Key):
    return await commands.cash(*tx, body, key)


@router.post("/cash/preview", response_model=s.FundsCashPreview)
async def preview_funds_cash(body: s.FundsCashInput, tx: Transaction):
    return await commands.preview_cash(*tx, body)


@router.post("/cash/{id}/reverse", response_model=s.FundsReceipt)
async def reverse_funds_cash(id: UUID, body: s.FundsReason, tx: Transaction, key: Key):
    return await commands.reverse_cash(*tx, id, body.reason, key)
