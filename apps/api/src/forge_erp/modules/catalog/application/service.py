import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.context import RuntimeContext
from forge_erp.core.errors import Problem
from forge_erp.core.idempotency import execute_once
from forge_erp.modules.audit.service import record_mutation
from forge_erp.modules.catalog.application.rules import (
    check_deactivation,
    project_prices,
    rules,
    search_text,
)
from forge_erp.modules.catalog.infrastructure.resources import RESOURCES


def json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


async def get_record(db: AsyncSession, ctx: RuntimeContext, resource: str, record_id: UUID) -> dict:
    spec = RESOURCES[resource]
    ctx.require(spec.permission + ".read")
    row = (
        (
            await db.execute(
                text(f"SELECT * FROM forge.{spec.table} WHERE organization_id=:org AND id=:id"),
                {"org": ctx.organization_id, "id": record_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "NOT_FOUND", "资料不存在或无权访问")
    return (
        await project_prices(
            db, ctx, resource, [{k: v for k, v in row.items() if k != "organization_id"}]
        )
    )[0]


async def list_records(
    db: AsyncSession,
    ctx: RuntimeContext,
    resource: str,
    q: str = "",
    page: int = 1,
    page_size: int = 25,
    active: bool | None = None,
    filters: dict | None = None,
) -> dict:
    if resource == "products":
        from forge_erp.modules.catalog.application.search import search_products

        return await search_products(db, ctx, q, page, page_size, active, filters)
    spec = RESOURCES[resource]
    ctx.require(spec.permission + ".read")
    params: dict[str, Any] = {
        "org": ctx.organization_id,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    clauses = ["organization_id=:org"]
    if active is not None:
        clauses.append("active=:active")
        params["active"] = active
    if q:
        params["q"] = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append("(" + " OR ".join(f"{f} ILIKE :q" for f in spec.search) + ")")
    for key, value in (filters or {}).items():
        if key not in spec.references or value is None:
            continue
        clauses.append(f"{key}=:{key}")
        params[key] = value
    where = " AND ".join(clauses)
    total = (
        await db.execute(text(f"SELECT count(*) FROM forge.{spec.table} WHERE {where}"), params)
    ).scalar_one()
    rows = (
        (
            await db.execute(
                text(
                    f"SELECT * FROM forge.{spec.table} WHERE {where} "
                    "ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": await project_prices(
            db,
            ctx,
            resource,
            [{k: v for k, v in row.items() if k != "organization_id"} for row in rows],
        ),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def validate_record(
    db: AsyncSession,
    ctx: RuntimeContext,
    resource: str,
    values: dict,
    record_id: UUID | None,
    previous: dict | None,
) -> None:
    spec = RESOURCES[resource]
    for key, table in spec.references.items():
        value = values.get(key)
        if value is not None:
            found = (
                await db.execute(
                    text(
                        f"SELECT id FROM forge.{table} "
                        "WHERE organization_id=:org AND id=:id AND active"
                    ),
                    {"org": ctx.organization_id, "id": value},
                )
            ).first()
            if found is None:
                raise Problem(409, "INVALID_REFERENCE", f"关联资料不可用：{key}")
    await rules(db, ctx, resource, values, record_id, previous)
    if resource == "categories":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
            {"scope": "catalog-write:" + str(ctx.organization_id)},
        )
        if record_id and values.get("parent_id"):
            cycle = (
                await db.execute(
                    text("""WITH RECURSIVE ancestors AS (
                SELECT id,parent_id FROM forge.categories WHERE organization_id=:org AND id=:parent
                UNION SELECT c.id,c.parent_id FROM forge.categories c JOIN ancestors a
                ON c.id=a.parent_id WHERE c.organization_id=:org)
                SELECT id FROM ancestors WHERE id=:id"""),
                    {"org": ctx.organization_id, "parent": values["parent_id"], "id": record_id},
                )
            ).first()
            if cycle:
                raise Problem(409, "CATEGORY_CYCLE", "分类不能形成循环层级")


async def write_command(
    db: AsyncSession,
    ctx: RuntimeContext,
    resource: str,
    values: dict,
    key: str,
    record_id: UUID | None = None,
    expected_version: int | None = None,
    active: bool | None = None,
) -> dict:
    spec = RESOURCES[resource]
    ctx.require(spec.permission + ".write")
    operation = (
        "create"
        if record_id is None
        else ("update" if active is None else "activate" if active else "deactivate")
    )

    async def execute() -> dict:
        if resource in RESOURCES:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
                {"scope": "catalog-write:" + str(ctx.organization_id)},
            )
        previous = None
        if record_id:
            row = (
                (
                    await db.execute(
                        text(
                            f"SELECT * FROM forge.{spec.table} "
                            "WHERE organization_id=:org AND id=:id FOR UPDATE"
                        ),
                        {"org": ctx.organization_id, "id": record_id},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise Problem(404, "NOT_FOUND", "资料不存在或无权访问")
            previous = dict(row)
            if previous["version"] != expected_version:
                raise Problem(409, "DOCUMENT_VERSION_CONFLICT", "资料已被他人修改，请刷新后重试")
        if active is None:
            values.update(spec.schema.model_validate(values).model_dump())
            await validate_record(db, ctx, resource, values, record_id, previous)
            changes: dict[str, Any] = values.copy()
        else:
            if previous is not None:
                if not active:
                    await check_deactivation(db, ctx, resource, previous)
                else:
                    await validate_record(
                        db,
                        ctx,
                        resource,
                        spec.schema.model_validate(
                            {k: previous[k] for k in spec.schema.model_fields}
                        ).model_dump(),
                        record_id,
                        previous,
                    )
            changes = {"active": active}
        if resource == "products" and active is None:
            changes["search_text"] = search_text(changes)
        columns = list(changes)
        allowed = set(spec.schema.model_fields) | {"active", "search_text"}
        if not set(columns) <= allowed:
            raise ValueError("Unrecognized application command field")
        binds = {
            name: (
                json.dumps(changes[name], default=str)
                if name in spec.json_fields
                else changes[name]
            )
            for name in columns
        }
        placeholders = {
            c: f"CAST(:{c} AS jsonb)" if c in spec.json_fields else f":{c}" for c in columns
        }
        if record_id is None:
            statement = f"INSERT INTO forge.{spec.table} (organization_id,{','.join(columns)}) "
            statement += f"VALUES (:org,{','.join(placeholders.values())}) RETURNING *"
        else:
            updates = ",".join(f"{c}={placeholders[c]}" for c in columns)
            statement = (
                f"UPDATE forge.{spec.table} SET {updates},version=version+1,updated_at=now() "
            )
            statement += "WHERE organization_id=:org AND id=:id RETURNING *"
        row = (
            (
                await db.execute(
                    text(statement), {**binds, "org": ctx.organization_id, "id": record_id}
                )
            )
            .mappings()
            .one()
        )
        if resource == "products" and record_id is None:
            await write_command(
                db,
                ctx,
                "product-units",
                {
                    "product_id": row["id"],
                    "unit_id": row["base_unit_id"],
                    "unit_to_base_factor": Decimal(1),
                    "notes": "基础单位",
                },
                str(row["id"]) + ":base",
            )
        result = {k: v for k, v in row.items() if k != "organization_id"}
        await record_mutation(
            db, ctx, f"catalog.{resource}.{operation}", resource, row["id"], previous, result
        )
        return json_value(result)

    try:
        result = await execute_once(
            db,
            ctx,
            f"{resource}.{operation}:{record_id or 'new'}",
            key,
            {"values": values, "expected_version": expected_version, "active": active},
            execute,
        )
        return (await project_prices(db, ctx, resource, [result]))[0]
    except (ValueError, ValidationError) as exc:
        raise Problem(422, "INVALID_CATALOG_DATA", str(exc)) from exc
    except IntegrityError as exc:
        raise Problem(409, "CATALOG_CONFLICT", "编码重复、关联资料无效或数据约束不满足") from exc
