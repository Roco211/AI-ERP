import json
from decimal import Decimal, localcontext
from uuid import UUID, uuid4

from sqlalchemy import text

from forge_erp.core.errors import Problem
from forge_erp.core.security import fingerprint
from forge_erp.modules.audit.service import record_mutation
from forge_erp.modules.catalog.domain.values import ConversionSnapshot
from forge_erp.modules.inventory.domain.values import InventoryError, exact
from forge_erp.modules.purchasing.application import orders
from forge_erp.modules.purchasing.domain.schemas import PurchaseOrderInput
from forge_erp.modules.purchasing.domain.values import line_amount
from forge_erp.modules.replenishment.application import queries
from forge_erp.modules.replenishment.domain.schemas import (
    HistoricalPrice,
    PreviewLine,
    PurchaseCreationInput,
    PurchasePreview,
    PurchasePreviewInput,
)

PREVIEW_SQL = (
    queries.BASIS_CTE
    + """
, selections AS (
 SELECT * FROM unnest(CAST(:ids AS uuid[]),CAST(:units AS uuid[])) AS x(product_id,unit_id)
), history AS (
 SELECT DISTINCT ON (il.product_id) il.product_id,d.id history_document_id,
 d.number history_document_number,pl.id history_line_id,d.posted_at history_posted_at,
 il.unit_id history_unit_id,il.unit_label history_unit_label,pl.unit_price history_unit_price,
 il.unit_to_base_factor history_factor,il.conversion_version history_conversion_version
 FROM forge.purchase_documents pd
 JOIN forge.purchase_orders po ON (po.organization_id,po.id)=(pd.organization_id,pd.order_id)
 JOIN forge.inventory_documents d ON (d.organization_id,d.id)=(pd.organization_id,pd.id)
 JOIN forge.purchase_document_lines pl
 ON (pl.organization_id,pl.document_id)=(pd.organization_id,pd.id)
 JOIN forge.inventory_document_lines il ON (il.organization_id,il.id)=(pl.organization_id,pl.id)
 JOIN selections sl ON sl.product_id=il.product_id
 WHERE pd.organization_id=:org AND pd.kind='RECEIPT'
 AND d.type='PURCHASE_RECEIPT' AND d.status='POSTED' AND po.supplier_id=:chosen_supplier
 ORDER BY il.product_id,d.posted_at DESC,d.id DESC,pl.id DESC
)
SELECT c.*,sl.unit_id,u.name selected_unit_name,u.version selected_unit_version,
 pu.unit_to_base_factor selected_factor,pu.version selected_conversion_version,
 s.id chosen_supplier_id,s.name chosen_supplier_name,s.version chosen_supplier_version,
 w.id chosen_warehouse_id,w.name chosen_warehouse_name,w.version chosen_warehouse_version,
 h.history_document_id,h.history_document_number,h.history_line_id,h.history_posted_at,
 h.history_unit_id,h.history_unit_label,h.history_unit_price,h.history_factor,
 h.history_conversion_version
FROM calculated c JOIN selections sl ON sl.product_id=c.product_id
JOIN forge.units u ON u.organization_id=:org AND u.id=sl.unit_id AND u.active
JOIN forge.suppliers s ON s.organization_id=:org AND s.id=:chosen_supplier AND s.active
JOIN forge.warehouses w ON w.organization_id=:org AND w.id=:chosen_warehouse AND w.active
LEFT JOIN forge.product_units pu ON pu.organization_id=:org AND pu.product_id=c.product_id
 AND pu.unit_id=sl.unit_id AND pu.active
LEFT JOIN history h ON h.product_id=c.product_id ORDER BY c.product_id
"""
)


def canonical(body: PurchasePreviewInput):
    data = body.model_dump(mode="json", exclude={"confirmation_hash"})
    data["lines"] = sorted(data["lines"], key=lambda line: line["product_id"])
    return data


def historical_price(row):
    if row["history_document_id"] is None:
        return None
    price = None
    if row["selected_factor"] is not None:
        try:
            with localcontext() as ctx:
                ctx.prec = 50
                price = exact(
                    row["history_unit_price"] * row["selected_factor"] / row["history_factor"]
                )
        except InventoryError:
            # Do not round a historical reference into a new commercial price.
            pass
    return HistoricalPrice(
        document_id=row["history_document_id"],
        document_number=row["history_document_number"],
        line_id=row["history_line_id"],
        posted_at=row["history_posted_at"],
        unit_id=row["history_unit_id"],
        unit_label=row["history_unit_label"],
        unit_price=row["history_unit_price"],
        unit_to_base_factor=row["history_factor"],
        conversion_version=row["history_conversion_version"],
        selected_unit_price=price,
    )


async def prepare_preview(db, ctx, body: PurchasePreviewInput):
    info = queries.window()
    inputs = {line.product_id: line for line in body.lines}
    ids = sorted(inputs)
    rows = (
        (
            await db.execute(
                text(PREVIEW_SQL),
                queries.params(ctx, info, ids=ids)
                | {
                    "units": [inputs[id].unit_id for id in ids],
                    "chosen_supplier": body.supplier_id,
                    "chosen_warehouse": body.warehouse_id,
                },
            )
        )
        .mappings()
        .all()
    )
    if len(rows) != len(ids):
        raise Problem(409, "INVALID_REFERENCE", "商品、供应商、仓库或单位不可用，请重新选择")
    lines = []
    raw_basis = []
    reference_snapshot = []
    for row in rows:
        item = inputs[row["product_id"]]
        basis = queries.suggestion(row, info)
        if item.basis_hash != basis.basis_hash:
            raise Problem(409, "REPLENISHMENT_BASIS_CHANGED", "补货依据已变化，请刷新并重新复核")
        raw_basis.append({k: row[k] for k in queries.BASIS_FIELDS})
        reference_snapshot.append(
            {
                k: v
                for k, v in row.items()
                if k not in queries.BASIS_FIELDS
                and k not in {"candidate", "point_n", "target_n", "suggested_base_qty"}
            }
        )
        blockers = []
        warnings = []
        factor = row["selected_factor"]
        qty, base_qty, amount = item.qty, None, None
        if factor is None:
            blockers.append("MISSING_UNIT_CONVERSION")
        else:
            try:
                if qty is None:
                    with localcontext() as dec:
                        dec.prec = 50
                        qty = exact(basis.suggested_base_qty / factor, positive=True)
                snap = ConversionSnapshot.capture(
                    item.product_id, item.unit_id, qty, factor, row["selected_conversion_version"]
                )
                base_qty = snap.base_qty
            except InventoryError, ValueError:
                qty = item.qty
                blockers.append(
                    "ZERO_SUGGESTION"
                    if basis.suggested_base_qty == 0 and item.qty is None
                    else "QUANTITY_CONVERSION_REQUIRED"
                )
        history = historical_price(row)
        price = item.unit_price
        if item.price_source == "HISTORY":
            price = history.selected_unit_price if history else None
            if price is None:
                blockers.append(
                    "HISTORY_PRICE_UNAVAILABLE"
                    if history is None
                    else "HISTORY_PRICE_PRECISION_CONFLICT"
                )
        elif price is None:
            blockers.append("PRICE_REQUIRED")
        if price is not None and base_qty is not None and qty is not None:
            try:
                amount = line_amount(qty, price)
            except InventoryError as exc:
                raise Problem(422, exc.code, exc.detail) from exc
        if body.supplier_id != basis.preferred_supplier_id:
            warnings.append("SUPPLIER_DIFFERS_FROM_BASIS")
        if basis.lead_days is None:
            warnings.append("MISSING_LEAD_DAYS")
        lines.append(
            PreviewLine(
                product_id=item.product_id,
                product_label=basis.sku + " · " + basis.name,
                basis=basis,
                unit_id=item.unit_id,
                unit_name=row["selected_unit_name"],
                unit_to_base_factor=factor,
                conversion_version=row["selected_conversion_version"],
                qty=qty,
                base_qty=base_qty,
                quantity_source="MANUAL" if item.qty is not None else "SUGGESTION",
                quantity_reason=item.quantity_reason,
                price_source=item.price_source,
                unit_price=price,
                historical_price=history,
                amount=amount,
                blocking_reasons=blockers,
                warnings=warnings,
            )
        )
    blockers = sorted({reason for line in lines for reason in line.blocking_reasons})
    total = None
    if not blockers:
        try:
            with localcontext() as dec:
                dec.prec = 50
                total = exact(
                    sum((line.amount for line in lines if line.amount is not None), Decimal(0)), 4
                )
        except InventoryError as exc:
            raise Problem(422, exc.code, exc.detail) from exc
    basis_snapshot = info.model_dump(mode="json") | {"products": raw_basis}
    selection = {
        "input": canonical(body),
        "references": reference_snapshot,
        "lines": [line.model_dump(mode="json") for line in lines],
        "total_amount": total,
    }
    confirmation = fingerprint(
        {
            "algorithm": info.algorithm_version,
            "window_start": info.window_start,
            "window_end": info.window_end,
            "selection": selection,
        }
    )
    first = rows[0]
    result = PurchasePreview(
        **info.model_dump(),
        supplier_id=body.supplier_id,
        supplier_name=first["chosen_supplier_name"],
        warehouse_id=body.warehouse_id,
        warehouse_name=first["chosen_warehouse_name"],
        reason=body.reason,
        lines=lines,
        total_amount=total,
        can_create=not blockers,
        blocking_reasons=blockers,
        confirmation_hash=confirmation,
    )
    return result, basis_snapshot, selection


async def preview(db, ctx, body: PurchasePreviewInput):
    queries.require_create(ctx)
    return (await prepare_preview(db, ctx, body))[0]


async def lock_references(db, ctx, body):
    # Same supplier -> warehouse -> product -> unit -> conversion order as purchase.save.
    # Batch row locks avoid adding a second per-product lookup loop. No inventory locks.
    for table, ids in (
        ("suppliers", [body.supplier_id]),
        ("warehouses", [body.warehouse_id]),
        ("products", [line.product_id for line in body.lines]),
        ("units", [line.unit_id for line in body.lines]),
    ):
        await db.execute(
            text(
                f"SELECT id FROM forge.{table} WHERE organization_id=:org "
                "AND id=ANY(CAST(:ids AS uuid[])) ORDER BY id FOR SHARE"
            ),
            {"org": ctx.organization_id, "ids": sorted(set(ids))},
        )
    ordered = sorted(body.lines, key=lambda line: (line.product_id, line.unit_id))
    await db.execute(
        text("""
 SELECT pu.id FROM forge.product_units pu
 JOIN unnest(CAST(:products AS uuid[]),CAST(:units AS uuid[])) AS x(product_id,unit_id)
 ON (x.product_id,x.unit_id)=(pu.product_id,pu.unit_id)
 WHERE pu.organization_id=:org ORDER BY pu.product_id,pu.unit_id FOR SHARE OF pu
 """),
        {
            "org": ctx.organization_id,
            "products": [line.product_id for line in ordered],
            "units": [line.unit_id for line in ordered],
        },
    )


async def create_purchase(db, ctx, body: PurchaseCreationInput, key: str):
    queries.require_create(ctx)
    request = canonical(body) | {"confirmation_hash": body.confirmation_hash}
    request_hash = fingerprint(request)

    async def execute():
        previous = (
            (
                await db.execute(
                    text("""
 SELECT request_hash,receipt FROM forge.replenishment_creations WHERE organization_id=:org
 AND actor_id=:actor AND idempotency_key=:key
 """),
                    {"org": ctx.organization_id, "actor": ctx.user_id, "key": key},
                )
            )
            .mappings()
            .first()
        )
        if previous:
            if previous["request_hash"] != request_hash:
                raise Problem(409, "IDEMPOTENCY_KEY_REUSED", "此键已用于另一补货创建请求")
            return dict(previous["receipt"])
        await lock_references(db, ctx, body)
        result, basis, selection = await prepare_preview(db, ctx, body)
        if result.confirmation_hash != body.confirmation_hash:
            raise Problem(
                409, "REPLENISHMENT_PREVIEW_CHANGED", "价格、单位或资料已变化，请重新预览确认"
            )
        if not result.can_create:
            raise Problem(409, "REPLENISHMENT_PREVIEW_BLOCKED", "请补齐价格、单位及数量后重新预览")
        creation_id = uuid4()
        purchase = PurchaseOrderInput.model_validate(
            {
                "supplier_id": body.supplier_id,
                "warehouse_id": body.warehouse_id,
                "reason": body.reason,
                "lines": [
                    {
                        "product_id": line.product_id,
                        "unit_id": line.unit_id,
                        "qty": line.qty,
                        "unit_price": line.unit_price,
                    }
                    for line in result.lines
                ],
            }
        )
        # Sole business write is the existing purchase DRAFT Command, in this transaction.
        receipt = await orders.save(db, ctx, purchase, "replenishment-" + creation_id.hex)
        receipt |= {"creation_id": str(creation_id)}
        await db.execute(
            text("""
 INSERT INTO forge.replenishment_creations
 (id,organization_id,actor_id,idempotency_key,request_hash,algorithm_version,confirmation_hash,
 basis_snapshot,selection_snapshot,purchase_order_id,receipt,request_id)
 VALUES(:id,:org,:actor,:key,:hash,:algorithm,:confirmation,CAST(:basis AS jsonb),
 CAST(:selection AS jsonb),:purchase,CAST(:receipt AS jsonb),:rid)
 """),
            {
                "id": creation_id,
                "org": ctx.organization_id,
                "actor": ctx.user_id,
                "key": key,
                "hash": request_hash,
                "algorithm": result.algorithm_version,
                "confirmation": result.confirmation_hash,
                "basis": json.dumps(basis, default=str),
                "selection": json.dumps(selection, default=str),
                "purchase": UUID(receipt["id"]),
                "receipt": json.dumps(receipt),
                "rid": ctx.request_id,
            },
        )
        await record_mutation(
            db,
            ctx,
            "replenishment.purchase.created",
            "replenishment_creation",
            creation_id,
            None,
            {
                "version": 1,
                "purchase_order_id": receipt["id"],
                "basis": basis,
                "selection": selection,
            },
        )
        return receipt

    return await orders.inventory_once(
        db, ctx, "replenishment.purchase.create", key, request, execute
    )
