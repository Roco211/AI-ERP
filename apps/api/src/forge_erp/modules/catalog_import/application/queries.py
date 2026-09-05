"""Read persistent results; summaries are derived from rows rather than raced counters."""

from sqlalchemy import text

from forge_erp.core.errors import Problem
from forge_erp.modules.catalog.infrastructure.resources import RESOURCES
from forge_erp.modules.catalog_import.domain.columns import require_read

SELECT_BATCH = """
SELECT b.*,clock_timestamp()<b.expires_at AND b.body_purged_at IS NULL body_available,
 count(r.id)::integer total,
 count(r.id) FILTER(WHERE r.status='READY')::integer ready,
 count(r.id) FILTER(WHERE r.status='INVALID')::integer invalid,
 count(r.id) FILTER(WHERE r.status='PENDING')::integer pending,
 count(r.id) FILTER(WHERE r.status='SUCCEEDED')::integer succeeded,
 count(r.id) FILTER(WHERE r.status='FAILED')::integer failed,
 coalesce(bool_or(r.status='FAILED' AND r.retryable),false) retryable,
 coalesce(bool_or(r.status='FAILED' AND r.error_code IN
 ('PERMISSION_DENIED','IMPORT_ACTOR_INACTIVE')),false) blocked
FROM forge.import_batches b LEFT JOIN forge.import_rows r
 ON r.organization_id=b.organization_id AND r.batch_id=b.id
WHERE b.organization_id=:org
"""


def present(ctx, row):
    result = dict(row)
    writable = {
        "catalog.import.write",
        RESOURCES[row["resource"]].permission + ".write",
    } <= ctx.permissions
    creator = row["created_by"] == ctx.user_id
    if not row["body_available"] and row["succeeded"] != row["total"]:
        status = "EXPIRED"
    elif row["confirmed_at"] is None:
        status = "PREVIEW_INVALID" if row["invalid"] else "PREVIEW_READY"
    elif row["blocked"]:
        status = "BLOCKED"
    elif row["pending"]:
        status = "RUNNING" if row["succeeded"] or row["failed"] else "QUEUED"
    elif row["failed"]:
        status = "PARTIAL_FAILED" if row["succeeded"] else "FAILED"
    else:
        status = "COMPLETED"
    result.update(
        status=status,
        is_creator=creator,
        can_confirm=writable and creator and status == "PREVIEW_READY",
        can_retry=writable and creator and row["body_available"] and row["retryable"],
    )
    return result


async def batch(db, ctx, id, *, lock=False):
    ctx.require("catalog.import.read")
    if lock:
        await db.execute(
            text(
                "SELECT id FROM forge.import_batches WHERE "
                "organization_id=:org AND id=:id FOR UPDATE"
            ),
            {"org": ctx.organization_id, "id": id},
        )
    row = (
        (
            await db.execute(
                text(SELECT_BATCH + " AND b.id=:id GROUP BY b.id"),
                {"org": ctx.organization_id, "id": id},
            )
        )
        .mappings()
        .first()
    )
    if not row:
        raise Problem(404, "NOT_FOUND", "导入批次不存在或无权访问")
    require_read(ctx, row["resource"])
    return present(ctx, row)


async def batches(db, ctx, page=1, page_size=25):
    ctx.require("catalog.import.read")
    allowed = [
        name
        for name, resource in RESOURCES.items()
        if resource.permission + ".read" in ctx.permissions
    ]
    base = SELECT_BATCH + " AND b.resource=ANY(CAST(:allowed AS text[])) GROUP BY b.id"
    rows = (
        (
            await db.execute(
                text(
                    "WITH results AS ("
                    + base
                    + "), page AS (SELECT * FROM results ORDER BY created_at DESC,id "
                    "DESC LIMIT :limit OFFSET :offset) SELECT p.*,n.total "
                    "count_total FROM (SELECT count(*) total FROM results)n "
                    "LEFT JOIN page p ON true ORDER BY p.created_at DESC,"
                    "p.id DESC"
                ),
                {
                    "org": ctx.organization_id,
                    "allowed": allowed,
                    "limit": page_size,
                    "offset": (page - 1) * page_size,
                },
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [present(ctx, row) for row in rows if row["id"]],
        "total": rows[0]["count_total"],
        "page": page,
        "page_size": page_size,
    }


async def rows(db, ctx, id, page=1, page_size=25, status=None):
    current = await batch(db, ctx, id)
    sql = "SELECT * FROM forge.import_rows WHERE organization_id=:org AND batch_id=:id"
    if status:
        sql += " AND status=:status"
    result = (
        (
            await db.execute(
                text(
                    "WITH results AS ("
                    + sql
                    + "), page AS (SELECT * FROM results ORDER BY row_no LIMIT :limit "
                    "OFFSET :offset) SELECT p.*,n.total FROM (SELECT "
                    "count(*) total FROM results)n LEFT JOIN page p ON true "
                    "ORDER BY p.row_no"
                ),
                {
                    "org": ctx.organization_id,
                    "id": id,
                    "status": status,
                    "limit": page_size,
                    "offset": (page - 1) * page_size,
                },
            )
        )
        .mappings()
        .all()
    )
    items = []
    for item in result:
        if item["id"] is not None:
            data = dict(item)
            if not current["body_available"]:
                data.update(raw_values=None, cleaned_values=None, errors=None, retryable=False)
            items.append(data)
    return {"items": items, "total": result[0]["total"], "page": page, "page_size": page_size}


async def export_values(db, ctx, id, failed_only=False):
    current = await batch(db, ctx, id)
    if not current["body_available"]:
        raise Problem(410, "PREVIEW_EXPIRED", "导入正文已到期，保留摘要与成功来源")
    records = (
        (
            await db.execute(
                text(
                    "SELECT row_no,status,raw_values,cleaned_values,errors "
                    "FROM forge.import_rows WHERE organization_id=:org AND "
                    "batch_id=:id"
                    + (" AND status IN ('FAILED','INVALID')" if failed_only else "")
                    + " ORDER BY row_no"
                ),
                {"org": ctx.organization_id, "id": id},
            )
        )
        .mappings()
        .all()
    )
    if not records:
        raise Problem(409, "NO_FAILED_ROWS", "没有可导出的失败行")
    headers = current["header_order"]
    if failed_only:
        return headers, [[(row["raw_values"] or {}).get(h) for h in headers] for row in records]
    cleaned = list(dict.fromkeys(k for row in records for k in (row["cleaned_values"] or {})))
    return [
        "源行号",
        "状态",
        "错误说明",
        *["原值:" + h for h in headers],
        *["清洗:" + h for h in cleaned],
    ], [
        [
            row["row_no"],
            row["status"],
            "; ".join(e["message"] for e in (row["errors"] or [])),
            *[(row["raw_values"] or {}).get(h) for h in headers],
            *[(row["cleaned_values"] or {}).get(h) for h in cleaned],
        ]
        for row in records
    ]
