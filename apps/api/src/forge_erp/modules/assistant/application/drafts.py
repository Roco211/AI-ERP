"""Deterministic previews and reviewed DRAFT creation through existing Commands."""

import hmac
from dataclasses import replace
from decimal import Decimal, localcontext
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.context import RuntimeContext
from forge_erp.core.errors import Problem
from forge_erp.core.security import fingerprint
from forge_erp.modules.assistant.domain.drafts import DraftInput, DraftLine, DraftPreview
from forge_erp.modules.catalog.domain.values import ConversionSnapshot
from forge_erp.modules.inventory.domain.values import InventoryError, exact
from forge_erp.modules.purchasing.application import orders as purchase_orders
from forge_erp.modules.purchasing.domain.values import line_amount as purchase_line_amount
from forge_erp.modules.sales.application import orders as sales_orders
from forge_erp.modules.sales.application import pricing
from forge_erp.modules.sales.domain.values import line_amount as sales_line_amount


def _canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _source(source: dict) -> dict:
    return {
        key: Decimal(value)
        if key in {"target_factor", "original_factor", "original_price"} and value is not None
        else value
        for key, value in source.items()
    }


def _require(ctx: RuntimeContext, kind: str) -> None:
    for permission in ("ai.use", "ai.draft.create", "catalog.read", "warehouse.read"):
        ctx.require(permission)
    if kind == "SALES":
        sales_orders.require(ctx, "sales.order.write")
        ctx.require("sales.read")
        ctx.require("customer.read")
    else:
        purchase_orders.require(ctx, "purchase.order.write")
        ctx.require("purchase.read")
        ctx.require("supplier.read")


async def _price_basis(db: AsyncSession, ctx: RuntimeContext, source: dict) -> dict | None:
    if source.get("source_id") is None:
        return None
    if source["source"] == "history":
        sql = (
            "SELECT id,version,status FROM forge.inventory_documents "
            "WHERE organization_id=:org AND id=:id FOR SHARE"
        )
        id = source["source_document_id"]
    else:
        sql = (
            "SELECT id,version,active FROM forge.product_prices "
            "WHERE organization_id=:org AND id=:id FOR SHARE"
        )
        id = source["source_id"]
    row = (await db.execute(text(sql), {"org": ctx.organization_id, "id": id})).mappings().first()
    if row is None:
        raise Problem(409, "AI_DRAFT_CHANGED", "报价依据已变化，请重新预览并复核")
    return dict(row)


async def preview_draft(db: AsyncSession, ctx: RuntimeContext, body: DraftInput) -> DraftPreview:
    """Locks use the corresponding order Command's reference order; no data is written."""
    _require(ctx, body.kind)
    if not body.order.reason.strip():
        raise Problem(422, "REASON_REQUIRED", "请填写开单原因")
    order = body.order
    if body.kind == "SALES":
        party_table, party_id = "customers", body.order.customer_id
        names = await sales_orders.refs(
            db,
            ctx,
            party_id,
            order.warehouse_id,
            [line.product_id for line in order.lines],
            [line.unit_id for line in order.lines],
        )
    else:
        party_table, party_id = "suppliers", body.order.supplier_id
        names = await purchase_orders.refs(
            db,
            ctx,
            party_id,
            order.warehouse_id,
            [line.product_id for line in order.lines],
            [line.unit_id for line in order.lines],
        )
    lines, basis = [], []
    warnings = ["仅创建草稿；不会确认订单、占用库存、出入库或收付款。"]
    try:
        for line in sorted(order.lines, key=lambda item: (item.product_id, item.unit_id)):
            conversion = await sales_orders.conversion(db, ctx, line.product_id, line.unit_id)
            snapshot = ConversionSnapshot.capture(
                line.product_id,
                line.unit_id,
                line.qty,
                conversion["unit_to_base_factor"],
                conversion["version"],
            )
            if body.kind == "SALES" and getattr(line, "pricing_mode", None) == "AUTO":
                quote = await pricing.quote(db, ctx, party_id, line.product_id, line.unit_id)
                unit_price, source = quote["unit_price"], _source(quote["price_source"])
                if unit_price is None:
                    raise Problem(409, "PRICE_UNSET", "商品未设置销售价，请明确输入并复核单价")
                price_basis = await _price_basis(db, ctx, source)
                mode = "AUTO"
            else:
                unit_price = line.unit_price
                if unit_price is None:
                    raise Problem(422, "PRICE_REQUIRED", "请明确填写并复核单价")
                source = {
                    "source": "manual",
                    "target_unit_id": str(line.unit_id),
                    "target_factor": conversion["unit_to_base_factor"],
                }
                price_basis, mode = None, "MANUAL"
            amount_fn = sales_line_amount if body.kind == "SALES" else purchase_line_amount
            lines.append(
                DraftLine(
                    **snapshot.model_dump(),
                    product_label=names[("products", line.product_id)]["sku"]
                    + " · "
                    + names[("products", line.product_id)]["name"],
                    unit_label=names[("units", line.unit_id)]["name"],
                    unit_price=unit_price,
                    amount=amount_fn(line.qty, unit_price),
                    pricing_mode=mode,
                    price_source=source,
                )
            )
            basis.append({"conversion_id": conversion["id"], "price": price_basis})
        with localcontext() as context:
            context.prec = 50
            total = exact(sum((line.amount for line in lines), Decimal(0)), 4)
    except ValidationError as exc:
        raise Problem(422, "INVALID_UNIT_QUANTITY", "单位换算后的数量超出精度或范围") from exc
    except InventoryError as exc:
        raise Problem(422, exc.code, exc.detail) from exc
    if any(line.pricing_mode == "MANUAL" for line in lines):
        warnings.append("手工单价来自本次输入，请核对原始报价；系统未将其认定为历史成交价。")
    preview = DraftPreview(
        kind=body.kind,
        order=body.order,
        party_name=names[(party_table, party_id)]["name"],
        warehouse_name=names[("warehouses", order.warehouse_id)]["name"],
        lines=lines,
        total_amount=total,
        confirmation_hash="",
        warnings=warnings,
    )
    preview.confirmation_hash = fingerprint(
        _canonical(
            {
                "format": "assistant-draft-v1",
                "organization_id": ctx.organization_id,
                "owner_id": ctx.user_id,
                "preview": preview.model_dump(),
                "line_basis": basis,
                "references": [
                    {"table": table, "id": id, "version": row["version"]}
                    for (table, id), row in sorted(names.items())
                ],
            }
        )
    )
    return preview


def _captured_line(line: dict, kind: str) -> dict:
    fields = (
        "product_id",
        "unit_id",
        "product_label",
        "unit_label",
        "qty",
        "unit_price",
        "amount",
        "unit_to_base_factor",
        "base_qty",
        "conversion_version",
    )
    result = {field: line[field] for field in fields}
    if kind == "SALES":
        result.update(pricing_mode=line["pricing_mode"], price_source=_source(line["price_source"]))
    return _canonical(result)


async def _verify_capture(
    db: AsyncSession, ctx: RuntimeContext, body: DraftInput, preview: DraftPreview, receipt: dict
) -> None:
    orders = sales_orders if body.kind == "SALES" else purchase_orders
    row = await orders.load(db, ctx, UUID(receipt["id"]))
    actual = await orders.raw_lines(db, ctx, UUID(receipt["id"]))
    if body.kind == "SALES":
        party_ok = (
            row["customer_id"] == body.order.customer_id
            and row["customer_name"] == preview.party_name
        )
    else:
        party_ok = (
            row["supplier_id"] == body.order.supplier_id
            and row["supplier_name"] == preview.party_name
        )
    if not (
        party_ok
        and row["status"] == "DRAFT"
        and row["version"] == 1
        and row["warehouse_id"] == body.order.warehouse_id
        and row["warehouse_name"] == preview.warehouse_name
        and row["reason"] == body.order.reason
        and row["amount"] == preview.total_amount
        and [_captured_line(line, body.kind) for line in actual]
        == [_captured_line(line.model_dump(), body.kind) for line in preview.lines]
    ):
        raise Problem(409, "AI_DRAFT_CHANGED", "创建时的资料或报价已变化，请重新预览并复核")


async def create_draft(
    db: AsyncSession, ctx: RuntimeContext, body: DraftInput, confirmation_hash: str, key: str
) -> dict:
    """Caller atomically records the permanent proposal receipt in this same transaction."""
    ctx = replace(ctx, source="AI")
    async with db.begin_nested():
        await db.execute(text("SET LOCAL lock_timeout = '5s'"))
        preview = await preview_draft(db, ctx, body)
        if not confirmation_hash.isascii() or not hmac.compare_digest(
            preview.confirmation_hash, confirmation_hash
        ):
            raise Problem(409, "AI_DRAFT_CHANGED", "资料、换算或报价已变化，请重新预览并复核")
        if body.kind == "SALES":
            receipt = await sales_orders.save(db, ctx, body.order, key, id=None)
        else:
            receipt = await purchase_orders.save(db, ctx, body.order, key, id=None)
        # New price/history rows can appear despite shared locks on existing references.
        # Compare the actual captured facts before releasing the savepoint; failures roll
        # back the Command, its audit/outbox, and its ordinary idempotency receipt together.
        await _verify_capture(db, ctx, body, preview, receipt)
        return receipt
