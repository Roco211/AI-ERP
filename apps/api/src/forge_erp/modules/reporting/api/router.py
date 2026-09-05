from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.auth_dependencies import authenticated_snapshot
from forge_erp.core.context import RuntimeContext
from forge_erp.modules.inventory.api.router import Page, Size
from forge_erp.modules.reporting.application import queries
from forge_erp.modules.reporting.domain.schemas import Metric, OperatingOverview, ReportSources

Snapshot = Annotated[
    tuple[AsyncSession, RuntimeContext], Depends(authenticated_snapshot, scope="function")
]
router = APIRouter(prefix="/api/v1/reporting", tags=["reporting"])


@router.get("/overview", response_model=OperatingOverview, response_model_exclude_none=True)
async def operating_overview(
    tx: Snapshot,
    date_from: date | None = None,
    date_to: date | None = None,
):
    return await queries.overview(*tx, date_from, date_to)


@router.get("/sources", response_model=ReportSources, response_model_exclude_none=True)
async def operating_sources(
    metric: Metric,
    tx: Snapshot,
    date_from: date | None = None,
    date_to: date | None = None,
    page: Page = 1,
    page_size: Size = 25,
):
    return await queries.sources(*tx, metric, date_from, date_to, page, page_size)
