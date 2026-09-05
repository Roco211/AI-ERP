import re
import time
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import sentry_sdk
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from starlette.exceptions import HTTPException

from forge_erp.core.config import settings
from forge_erp.core.db import engine, verify_database_role
from forge_erp.core.errors import Problem, ProblemDetails
from forge_erp.core.observability import configure_logging
from forge_erp.modules.catalog.api.router import router as catalog_router
from forge_erp.modules.catalog.api.search_router import router as catalog_search_router
from forge_erp.modules.catalog_import.api.router import router as catalog_import_router
from forge_erp.modules.funds.api.router import router as funds_router
from forge_erp.modules.identity.api.router import router
from forge_erp.modules.inventory.api.router import router as inventory_router
from forge_erp.modules.purchasing.api.router import router as purchasing_router
from forge_erp.modules.replenishment.api.router import router as replenishment_router
from forge_erp.modules.reporting.api.router import router as reporting_router
from forge_erp.modules.sales.api.router import router as sales_router

configure_logging()
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if len(settings().session_secret) < 32:
        raise RuntimeError("SESSION_SECRET must contain at least 32 random characters")
    await verify_database_role()
    if settings().sentry_dsn:
        sentry_sdk.init(
            dsn=settings().sentry_dsn, send_default_pii=False, max_request_body_size="never"
        )
    yield
    await engine.dispose()


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {
        "model": ProblemDetails,
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    }
    for code in (401, 403, 409, 422, 429, 500, 503)
}
app = FastAPI(title="Forge ERP", version="0.10.0", lifespan=lifespan, responses=ERROR_RESPONSES)
app.include_router(router)
app.include_router(catalog_router)
app.include_router(inventory_router)
app.include_router(purchasing_router)
app.include_router(sales_router)
app.include_router(funds_router)
app.include_router(reporting_router)
app.include_router(replenishment_router)
app.include_router(catalog_import_router)


def problem_response(request: Request, status: int, code: str, detail: str) -> JSONResponse:
    body = ProblemDetails(
        type=f"/errors/{code}",
        title=code.replace("_", " ").title(),
        status=status,
        code=code,
        detail=detail,
        request_id=getattr(request.state, "request_id", str(uuid4())),
    )
    return JSONResponse(
        status_code=status, content=body.model_dump(), media_type="application/problem+json"
    )


@app.exception_handler(Problem)
async def handle_problem(request: Request, exc: Problem):
    return problem_response(request, exc.status, exc.code, exc.detail)


@app.exception_handler(RequestValidationError)
async def handle_validation(request: Request, exc: RequestValidationError):
    # Validation inputs can contain passwords; never reflect/log them.
    return problem_response(request, 422, "VALIDATION_ERROR", "Request validation failed")


@app.exception_handler(HTTPException)
async def handle_http(request: Request, exc: HTTPException):
    return problem_response(request, exc.status_code, "HTTP_ERROR", "Request could not be handled")


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    candidate = request.headers.get("X-Request-ID", "")
    rid = candidate if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", candidate) else str(uuid4())
    request.state.request_id = rid
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=rid)
    start = time.monotonic()
    try:
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (
            request.headers.get("origin") != settings().web_origin
        ):
            response = problem_response(request, 403, "CSRF_REJECTED", "Invalid request origin")
        else:
            response = await call_next(request)
    except Exception as exc:
        # Do not log SQL parameters, cookies, query strings or exception messages.
        log.error("request_failed", error_type=type(exc).__name__)
        response = problem_response(request, 500, "INTERNAL_ERROR", "Unexpected server error")
    response.headers["X-Request-ID"] = rid
    response.headers["Cache-Control"] = "no-store"
    log.info(
        "http_request",
        method=request.method,
        status=response.status_code,
        duration_ms=round((time.monotonic() - start) * 1000, 2),
    )
    return response


class Health(BaseModel):
    status: str


class Version(BaseModel):
    name: str = "Forge ERP"
    version: str = "0.10.0"
    milestone: str = "Operations"


@app.get("/healthz", response_model=Health, operation_id="healthz")
async def healthz() -> Health:
    return Health(status="ok")


@app.get("/readyz", response_model=Health, operation_id="readyz")
async def readyz() -> Health:
    try:
        async with engine.connect() as db:
            await db.execute(text("SELECT 1 FROM forge.permissions LIMIT 1"))
        async with Redis.from_url(settings().redis_url) as redis:
            await redis.ping()
    except Exception as exc:
        raise Problem(503, "NOT_READY", "Required dependency unavailable") from exc
    return Health(status="ready")


@app.get("/api/v1/system/version", response_model=Version, operation_id="systemVersion")
async def version() -> Version:
    return Version()


FastAPIInstrumentor.instrument_app(app)


app.include_router(catalog_search_router)
