"""Explicit immutable preview confirmation and retry of retryable failed rows."""

from sqlalchemy import text

from forge_erp.core.errors import Problem
from forge_erp.core.idempotency import execute_once
from forge_erp.core.security import fingerprint
from forge_erp.modules.audit.service import record_mutation
from forge_erp.modules.catalog_import.application import preview, queries
from forge_erp.modules.catalog_import.domain.columns import require_write


def receipt(ctx, batch, status=None):
    return {
        "id": str(batch["id"]),
        "status": status or batch["status"],
        "version": batch["version"],
        "request_id": ctx.request_id,
    }


def author(ctx, batch):
    require_write(ctx, batch["resource"])
    if batch["created_by"] != ctx.user_id:
        raise Problem(403, "IMPORT_CREATOR_REQUIRED", "只能由批次创建者确认或重试")


async def confirm(db, ctx, id, body, key):
    initial = await queries.batch(db, ctx, id)
    author(ctx, initial)

    async def execute():
        current = await queries.batch(db, ctx, id, lock=True)
        digest = fingerprint(body.model_dump(mode="json"))
        if current["confirmed_at"]:
            if current["confirmation_key"] == key:
                if current["confirmation_hash"] != digest:
                    raise Problem(409, "IDEMPOTENCY_KEY_REUSED", "原确认键不能用于其他内容")
                return {
                    "id": str(id),
                    "status": "QUEUED",
                    "version": 2,
                    "request_id": current["confirmation_request_id"],
                }
            raise Problem(409, "IMPORT_ALREADY_CONFIRMED", "批次已经确认，请查看持久进度")
        if not current["body_available"]:
            raise Problem(409, "PREVIEW_EXPIRED", "预览正文已到期，请重新上传")
        if (
            current["version"] != body.expected_version
            or current["preview_hash"] != body.preview_hash
        ):
            raise Problem(409, "VERSION_CONFLICT", "预览已变化，请重新读取核对")
        if current["invalid"] or current["total"] == 0:
            raise Problem(409, "PREVIEW_HAS_ERRORS", "请先修正全部预览错误并重新上传")
        await db.execute(
            text(
                "UPDATE forge.import_batches SET "
                "confirmed_at=clock_timestamp(),confirmation_key=:key,"
                "confirmation_hash=:hash,confirmation_request_id=:rid,"
                "version=version+1 WHERE organization_id=:org AND id=:id"
            ),
            {
                "org": ctx.organization_id,
                "id": id,
                "key": key,
                "hash": digest,
                "rid": ctx.request_id,
            },
        )
        await db.execute(
            text(
                "UPDATE forge.import_rows SET status='PENDING' WHERE "
                "organization_id=:org AND batch_id=:id AND "
                "status='READY'"
            ),
            {"org": ctx.organization_id, "id": id},
        )
        await record_mutation(
            db,
            ctx,
            "catalog.import.confirm",
            "catalog_import",
            id,
            None,
            {"version": current["version"] + 1, "rows": current["total"]},
        )
        return receipt(ctx, {**current, "version": current["version"] + 1}, "QUEUED")

    return await execute_once(
        db, ctx, "catalog.import.confirm:" + str(id), key, body.model_dump(mode="json"), execute
    )


async def retry(db, ctx, id, body, key):
    initial = await queries.batch(db, ctx, id)
    author(ctx, initial)

    async def execute():
        current = await queries.batch(db, ctx, id, lock=True)
        if not current["body_available"]:
            raise Problem(409, "PREVIEW_EXPIRED", "预览正文已到期，请重新上传")
        if current["version"] != body.expected_version:
            raise Problem(409, "VERSION_CONFLICT", "批次已变化，请重新读取")
        if not current["confirmed_at"]:
            raise Problem(409, "IMPORT_NOT_CONFIRMED", "请先确认预览")
        if len(set(body.row_ids)) != len(body.row_ids):
            raise Problem(422, "DUPLICATE_ROW", "重试行不能重复")
        selected = (
            (
                await db.execute(
                    text(
                        "SELECT id,status,retryable FROM forge.import_rows "
                        "WHERE organization_id=:org AND batch_id=:batch AND "
                        "id=ANY(CAST(:ids AS uuid[])) ORDER BY row_no FOR UPDATE"
                    ),
                    {
                        "org": ctx.organization_id,
                        "batch": id,
                        "ids": [str(row) for row in body.row_ids],
                    },
                )
            )
            .mappings()
            .all()
        )
        if len(selected) != len(body.row_ids) or any(
            row["status"] != "FAILED" or not row["retryable"] for row in selected
        ):
            raise Problem(
                409, "ROW_NOT_RETRYABLE", "只能重试本批次可重试的失败行；版本或引用冲突须重新预览"
            )
        await db.execute(
            text(
                "UPDATE forge.import_rows SET status='PENDING',"
                "error_code=NULL,errors='[]'::jsonb,retryable=false "
                "WHERE organization_id=:org AND batch_id=:batch AND "
                "id=ANY(CAST(:ids AS uuid[]))"
            ),
            {"org": ctx.organization_id, "batch": id, "ids": [str(row) for row in body.row_ids]},
        )
        await db.execute(
            text(
                "UPDATE forge.import_batches SET version=version+1 "
                "WHERE organization_id=:org AND id=:id"
            ),
            {"org": ctx.organization_id, "id": id},
        )
        await record_mutation(
            db,
            ctx,
            "catalog.import.retry",
            "catalog_import",
            id,
            None,
            {"version": current["version"] + 1, "rows": len(selected)},
        )
        return receipt(ctx, {**current, "version": current["version"] + 1}, "QUEUED")

    return await execute_once(
        db, ctx, "catalog.import.retry:" + str(id), key, body.model_dump(mode="json"), execute
    )


async def upload(db, ctx, resource, mode, sheet, key):
    require_write(ctx, resource)
    payload = {
        "resource": resource,
        "mode": mode,
        "file_hash": sheet.file_hash,
        "filename": sheet.filename,
        "worksheet": sheet.worksheet,
    }
    return await execute_once(
        db,
        ctx,
        "catalog.import.preview",
        key,
        payload,
        lambda: preview.save_preview(db, ctx, resource, mode, sheet),
    )
