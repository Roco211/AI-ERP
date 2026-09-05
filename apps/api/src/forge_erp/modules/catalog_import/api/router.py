"""Authenticated bounded file transport and thin import command routes."""

from typing import Annotated, Any, get_args
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import ValidationError
from python_multipart.exceptions import MultipartParseError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request as FormRequest

from forge_erp.core.auth_dependencies import authenticated_snapshot
from forge_erp.core.context import RuntimeContext
from forge_erp.core.errors import Problem
from forge_erp.modules.catalog_import.application import commands, queries
from forge_erp.modules.catalog_import.domain import schemas as s
from forge_erp.modules.catalog_import.domain.columns import require_read, require_write
from forge_erp.modules.catalog_import.infrastructure.xlsx import (
    MAX_UPLOAD,
    parse,
    template,
    workbook_bytes,
)
from forge_erp.modules.inventory.api.router import Key, Page, Size, Transaction

Snapshot = Annotated[
    tuple[AsyncSession, RuntimeContext], Depends(authenticated_snapshot, scope="function")
]
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSX_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {"content": {XLSX_TYPE: {"schema": {"type": "string", "format": "binary"}}}}
}

router = APIRouter(prefix="/api/v1/catalog-imports", tags=["catalog-imports"])


class UploadOptions(s.Input):
    resource: s.ResourceName
    mode: s.Mode = "CREATE_ONLY"
    worksheet: str | None = None


async def bounded_form(request, ctx):
    ctx.require("catalog.import.read")
    ctx.require("catalog.import.write")
    maximum = MAX_UPLOAD + 100000
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise Problem(422, "MULTIPART_REQUIRED", "请使用文件上传表单")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > maximum:
            raise Problem(413, "FILE_TOO_LARGE", "上传请求超过文件与表单大小限制")
        body.extend(chunk)
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    transport = FormRequest(request.scope, receive)
    try:
        async with transport.form(max_files=1, max_fields=3, max_part_size=4096) as form:
            if set(form) - {"file", "resource", "mode", "worksheet"} or len(
                form.multi_items()
            ) != len(form):
                raise Problem(422, "INVALID_FORM", "上传表单包含未知或重复字段")
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise Problem(422, "FILE_REQUIRED", "请选择一个 xlsx 文件")
            try:
                options = UploadOptions.model_validate(
                    {key: form[key] for key in ("resource", "mode", "worksheet") if key in form}
                )
            except ValidationError as exc:
                raise Problem(422, "INVALID_FORM", "请选择有效资料类型、导入模式和工作表") from exc
            require_write(ctx, options.resource)
            data = await file.read(MAX_UPLOAD + 1)
            if len(data) > MAX_UPLOAD:
                raise Problem(413, "FILE_TOO_LARGE", "文件最多 10,000,000 字节")
            try:
                sheet = await run_in_threadpool(parse, data, file.filename or "", options.worksheet)
            except Problem:
                raise
            except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
                raise Problem(422, "INVALID_XLSX", "工作簿结构或单元格内容不合法") from exc
    except MultipartParseError as exc:
        raise Problem(422, "INVALID_FORM", "上传表单结构不合法") from exc
    # The context closes and removes every multipart temporary upload, including error paths.
    return options, sheet


UPLOAD_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file", "resource"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "resource": {"type": "string", "enum": list(get_args(s.ResourceName))},
                        "mode": {"type": "string", "enum": ["CREATE_ONLY", "UPDATE_EXISTING"]},
                        "worksheet": {"type": "string"},
                    },
                }
            }
        },
    }
}


@router.post(
    "/previews", response_model=s.BatchReceipt, status_code=201, openapi_extra=UPLOAD_SCHEMA
)
async def upload_preview(request: Request, tx: Transaction, key: Key):
    db, ctx = tx
    options, sheet = await bounded_form(request, ctx)
    return await commands.upload(db, ctx, options.resource, options.mode, sheet, key)


def download(data, filename):
    return Response(
        data,
        media_type=XLSX_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/templates/{resource}", response_class=Response, responses=XLSX_RESPONSE)
async def download_template(resource: s.ResourceName, tx: Snapshot):
    require_read(tx[1], resource)
    return download(await run_in_threadpool(template, resource), resource + "-template.xlsx")


@router.get("", response_model=s.BatchesPage)
async def list_batches(tx: Snapshot, page: Page = 1, page_size: Size = 25):
    return await queries.batches(*tx, page, page_size)


@router.get("/{id}", response_model=s.BatchRead)
async def get_batch(id: UUID, tx: Snapshot):
    return await queries.batch(*tx, id)


@router.get("/{id}/rows", response_model=s.RowsPage)
async def get_rows(
    id: UUID,
    tx: Snapshot,
    page: Page = 1,
    page_size: Size = 25,
    status: s.RowStatus | None = None,
):
    return await queries.rows(*tx, id, page, page_size, status)


@router.post("/{id}/confirm", response_model=s.BatchReceipt)
async def confirm_import(id: UUID, body: s.ConfirmInput, tx: Transaction, key: Key):
    return await commands.confirm(*tx, id, body, key)


@router.post("/{id}/retry", response_model=s.BatchReceipt)
async def retry_import(id: UUID, body: s.RetryInput, tx: Transaction, key: Key):
    return await commands.retry(*tx, id, body, key)


@router.get("/{id}/result.xlsx", response_class=Response, responses=XLSX_RESPONSE)
async def export_results(id: UUID, tx: Snapshot, failed_only: Annotated[bool, Query()] = False):
    headers, values = await queries.export_values(*tx, id, failed_only)
    return download(
        await run_in_threadpool(workbook_bytes, headers, values),
        "import-failed.xlsx" if failed_only else "import-result.xlsx",
    )
