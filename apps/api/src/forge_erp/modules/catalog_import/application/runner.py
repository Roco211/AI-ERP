"""Database-backed row execution; queue delivery and TTL are never success evidence."""

from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from forge_erp.core.context import RuntimeContext
from forge_erp.core.db import set_tenant
from forge_erp.core.errors import Problem
from forge_erp.modules.audit.service import record_mutation
from forge_erp.modules.catalog.application.service import write_command
from forge_erp.modules.catalog_import.application.preview import error, json_dump
from forge_erp.modules.catalog_import.domain.columns import require_write
from forge_erp.modules.identity.application.commands import load_permissions

REFERENCE_TABLES = {
    "categories",
    "brands",
    "units",
    "customers",
    "suppliers",
    "warehouses",
    "products",
    "product_units",
}
BLOCKED = {"PERMISSION_DENIED", "IMPORT_ACTOR_INACTIVE"}
RETRYABLE = BLOCKED | {"IMPORT_TEMPORARY_FAILURE", "STOCK_BUSY", "IDEMPOTENCY_BUSY"}


async def actor(db, org, user, request_id):
    enabled = (
        await db.execute(
            text(
                "SELECT u.id FROM forge.users u JOIN "
                "forge.organizations o ON o.id=u.organization_id WHERE "
                "u.organization_id=:org AND u.id=:user AND u.active AND "
                "o.active"
            ),
            {"org": org, "user": user},
        )
    ).first()
    if not enabled:
        raise Problem(403, "IMPORT_ACTOR_INACTIVE", "创建者或组织已停用，恢复后才能重试剩余行")
    return RuntimeContext(
        org, user, await load_permissions(db, org, user), request_id, source="API"
    )


async def claim(db, org):
    return (
        (
            await db.execute(
                text("""
                WITH eligible AS MATERIALIZED (
                 SELECT b.id,b.organization_id,b.resource,b.created_by,b.expires_at,b.created_at
                 FROM forge.import_batches b
                 WHERE b.organization_id=:org AND b.confirmed_at IS NOT NULL
                  AND b.expires_at>clock_timestamp() AND b.body_purged_at IS NULL
                  AND NOT EXISTS (
                   SELECT 1 FROM forge.import_rows blocked
                   WHERE blocked.organization_id=b.organization_id AND blocked.batch_id=b.id
                    AND blocked.status='FAILED'
                    AND blocked.error_code IN ('PERMISSION_DENIED','IMPORT_ACTOR_INACTIVE'))
                )
                SELECT r.*,b.resource,b.created_by,b.expires_at
                FROM eligible b JOIN forge.import_rows r
                 ON r.organization_id=b.organization_id AND r.batch_id=b.id
                WHERE r.organization_id=:org AND r.status='PENDING'
                ORDER BY b.created_at,b.id,r.row_no LIMIT 1 FOR UPDATE OF r SKIP LOCKED
                """),
                {"org": org},
            )
        )
        .mappings()
        .first()
    )


async def execute_row(db, org, row):
    # Check once before any cached result, and again after waiting for the ordinary catalog lock.
    ctx = await actor(db, org, row["created_by"], "import-" + str(uuid4()))
    require_write(ctx, row["resource"])
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
        {"scope": "catalog-write:" + str(org)},
    )
    ctx = await actor(db, org, row["created_by"], ctx.request_id)
    require_write(ctx, row["resource"])
    if not row["command_values"] or row["body_purged_at"]:
        raise Problem(409, "PREVIEW_EXPIRED", "预览正文已到期")
    valid = (
        await db.execute(
            text(
                "SELECT expires_at>clock_timestamp() FROM "
                "forge.import_batches WHERE organization_id=:org AND "
                "id=:batch"
            ),
            {"org": org, "batch": row["batch_id"]},
        )
    ).scalar_one()
    if not valid:
        raise Problem(409, "PREVIEW_EXPIRED", "预览正文已到期")
    for reference in row["references_snapshot"]:
        table = reference["table"]
        if table not in REFERENCE_TABLES:
            raise Problem(409, "REFERENCE_CHANGED", "预览引用结构不合法")
        actual = (
            (
                await db.execute(
                    text(
                        f"SELECT version,active FROM forge.{table} "
                        "WHERE organization_id=:org AND id=:id"
                    ),
                    {"org": org, "id": reference["id"]},
                )
            )
            .mappings()
            .first()
        )
        if not actual or not actual["active"] or actual["version"] != reference["version"]:
            raise Problem(409, "REFERENCE_CHANGED", "关联资料或分类模板已变化，请重新预览")
    result = await write_command(
        db,
        ctx,
        row["resource"],
        dict(row["command_values"]),
        "import:" + str(row["batch_id"]) + ":" + str(row["id"]) + ":" + row["action"],
        row["expected_target_id"] if row["action"] == "UPDATE" else None,
        row["expected_version"],
    )
    await db.execute(
        text(
            "UPDATE forge.import_rows SET status='SUCCEEDED',"
            "target_id=:target,target_version=:version,"
            "completed_at=clock_timestamp(),request_id=:rid,"
            "attempts=attempts+1,errors='[]'::jsonb,error_code=NULL,"
            "retryable=false WHERE organization_id=:org AND id=:id "
            "AND status='PENDING'"
        ),
        {
            "org": org,
            "id": row["id"],
            "target": result["id"],
            "version": result["version"],
            "rid": ctx.request_id,
        },
    )
    await record_mutation(
        db,
        ctx,
        "catalog.import.row.succeeded",
        "catalog_import_row",
        row["id"],
        None,
        {
            "version": 1,
            "batch_id": str(row["batch_id"]),
            "row_no": row["row_no"],
            "target_id": result["id"],
            "target_version": result["version"],
        },
    )


def failure(exc):
    if isinstance(exc, Problem):
        code = "VERSION_CONFLICT" if exc.code == "DOCUMENT_VERSION_CONFLICT" else exc.code
        return code, exc.detail, code in RETRYABLE
    if isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) in {
        "23505",
        "23503",
        "23514",
    }:
        return "CATALOG_CONFLICT", "资料约束已变化，请重新预览", False
    return (
        "IMPORT_TEMPORARY_FAILURE",
        "本行执行暂未完成，请重试原行；如已提交会保留原成功结果",
        True,
    )


async def record_failure(factory, org, row, exc):
    code, detail, retryable = failure(exc)
    async with factory.begin() as db:
        await set_tenant(db, org)
        current = (
            await db.execute(
                text(
                    "SELECT status FROM forge.import_rows WHERE "
                    "organization_id=:org AND id=:id FOR UPDATE"
                ),
                {"org": org, "id": row["id"]},
            )
        ).scalar_one()
        if current != "PENDING":
            return current
        await db.execute(
            text(
                "UPDATE forge.import_rows SET status='FAILED',"
                "error_code=:code,errors=CAST(:errors AS jsonb),"
                "retryable=:retryable,attempts=attempts+1 WHERE "
                "organization_id=:org AND id=:id AND status='PENDING'"
            ),
            {
                "org": org,
                "id": row["id"],
                "code": code,
                "errors": json_dump([error("", code, detail)]),
                "retryable": retryable,
            },
        )
    return "FAILED"


async def run_one(factory, org):
    row = None
    try:
        async with factory.begin() as db:
            await set_tenant(db, org)
            claimed = await claim(db, org)
            if claimed is None:
                return None
            row = dict(claimed)
            await execute_row(db, org, row)
        return "SUCCEEDED"
    except Exception as exc:
        if row is None:
            raise
        return await record_failure(factory, org, row, exc)


async def purge(factory, org, limit=1000):
    # No row transaction also locks its parent batch; retry locks in the other direction.
    async with factory.begin() as db:
        await set_tenant(db, org)
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT r.id FROM forge.import_rows r JOIN "
                        "forge.import_batches b ON "
                        "b.organization_id=r.organization_id AND "
                        "b.id=r.batch_id WHERE r.organization_id=:org AND "
                        "b.expires_at<=clock_timestamp() AND r.body_purged_at "
                        "IS NULL ORDER BY r.id LIMIT :limit FOR UPDATE OF r "
                        "SKIP LOCKED"
                    ),
                    {"org": org, "limit": limit},
                )
            )
            .scalars()
            .all()
        )
        if rows:
            await db.execute(
                text(
                    "UPDATE forge.import_rows SET raw_values=NULL,"
                    "cleaned_values=NULL,command_values=NULL,"
                    "references_snapshot=NULL,locator=NULL,errors=NULL,"
                    "retryable=false,body_purged_at=clock_timestamp() WHERE "
                    "organization_id=:org AND id=ANY(CAST(:ids AS uuid[]))"
                ),
                {"org": org, "ids": [str(id) for id in rows]},
            )
    async with factory.begin() as db:
        await set_tenant(db, org)
        await db.execute(
            text(
                "UPDATE forge.import_batches b SET "
                "body_purged_at=clock_timestamp() WHERE "
                "b.organization_id=:org AND "
                "b.expires_at<=clock_timestamp() AND b.body_purged_at "
                "IS NULL AND NOT EXISTS(SELECT 1 FROM forge.import_rows "
                "r WHERE r.organization_id=b.organization_id AND "
                "r.batch_id=b.id AND r.body_purged_at IS NULL)"
            ),
            {"org": org},
        )
    return len(rows)


async def drain(factory, organization_id: UUID | None = None, limit=100):
    async with factory.begin() as db:
        role = (
            await db.execute(
                text(
                    "SELECT rolname,rolsuper,rolbypassrls,rolcreatedb,"
                    "rolcreaterole FROM pg_roles WHERE rolname=current_user"
                )
            )
        ).one()
        if role[0] != "forge_app" or any(role[1:]):
            raise RuntimeError("Import worker requires restricted forge_app")
        orgs = (
            (await db.execute(text("SELECT organization_id FROM forge.pending_import_tenants()")))
            .scalars()
            .all()
        )
    result = {"succeeded": 0, "failed": 0, "purged": 0}
    for org in orgs:
        if organization_id is not None and org != organization_id:
            continue
        result["purged"] += await purge(factory, org)
        for _ in range(limit):
            status = await run_one(factory, org)
            if status is None:
                break
            result["succeeded" if status == "SUCCEEDED" else "failed"] += 1
    return result
