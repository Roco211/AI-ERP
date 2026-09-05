"""The only writer of inventory projections. Caller owns the transaction."""

import json
from decimal import Decimal, localcontext
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.context import RuntimeContext
from forge_erp.core.errors import Problem
from forge_erp.modules.audit.service import record_mutation
from forge_erp.modules.inventory.domain.values import ZERO, Balance, InventoryError, exact, rounded

Key = tuple[UUID, UUID]


class InventoryEngine:
    def __init__(self, db: AsyncSession, ctx: RuntimeContext, permission: str = "inventory.adjust"):
        if permission not in {
            "inventory.adjust",
            "inventory.opening",
            "inventory.transfer",
            "inventory.stocktake",
            "inventory.reverse",
            "inventory.reconcile",
        }:
            raise ValueError("Invalid inventory capability")
        self.permission = permission
        self.db, self.ctx = db, ctx
        self.rows: dict[Key, dict] = {}

    async def lock(self, keys: list[Key], *, historical: bool = False) -> None:
        self.ctx.require(self.permission)
        ordered = sorted(set(keys))
        for table, ids in [
            ("warehouses", {k[0] for k in ordered}),
            ("products", {k[1] for k in ordered}),
        ]:
            for id in sorted(ids):
                row = (
                    await self.db.execute(
                        text(
                            f"SELECT active FROM forge.{table} "
                            "WHERE organization_id=:org AND id=:id FOR SHARE"
                        ),
                        {"org": self.ctx.organization_id, "id": id},
                    )
                ).first()
                if row is None or (not historical and not row.active):
                    raise Problem(409, "INVALID_REFERENCE", "商品或仓库不存在、已停用或无权使用")
        for wh, product in ordered:
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"inventory:{self.ctx.organization_id}:{wh}:{product}"},
            )
            await self.db.execute(
                text(
                    "INSERT INTO forge.inventory_balances "
                    "(organization_id,warehouse_id,product_id) VALUES (:org,:wh,:product) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"org": self.ctx.organization_id, "wh": wh, "product": product},
            )
            row = (
                (
                    await self.db.execute(
                        text(
                            "SELECT * FROM forge.inventory_balances "
                            "WHERE organization_id=:org AND warehouse_id=:wh AND "
                            "product_id=:product FOR UPDATE"
                        ),
                        {"org": self.ctx.organization_id, "wh": wh, "product": product},
                    )
                )
                .mappings()
                .one()
            )
            self.rows[(wh, product)] = dict(row)

    def state(self, key: Key) -> Balance:
        row = self.rows[key]
        return Balance(
            row["on_hand_qty"], row["reserved_qty"], row["inventory_value"], row["avg_unit_cost"]
        )

    async def source(self, line_id: UUID, key: Key) -> dict:
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT l.*,d.warehouse_id,d.target_warehouse_id "
                        "FROM forge.inventory_document_lines l JOIN forge.inventory_documents d "
                        "ON (d.organization_id,d.id)=(l.organization_id,l.document_id) "
                        "WHERE l.organization_id=:org AND l.id=:id"
                    ),
                    {"org": self.ctx.organization_id, "id": line_id},
                )
            )
            .mappings()
            .first()
        )
        if (
            row is None
            or row["product_id"] != key[1]
            or key[0] not in (row["warehouse_id"], row["target_warehouse_id"])
        ):
            raise Problem(404, "NOT_FOUND", "库存来源不存在或无权使用")
        return dict(row)

    async def change(
        self,
        key: Key,
        line_id: UUID,
        operation: UUID,
        kind: str,
        qty: Decimal,
        *,
        cost: Decimal | None = None,
        value: Decimal | None = None,
        reservation_id: UUID | None = None,
    ) -> dict:
        self.ctx.require(self.permission)
        source = await self.source(line_id, key)
        old = self.state(key)
        reservation = None
        if reservation_id:
            reservation = (
                (
                    await self.db.execute(
                        text(
                            "SELECT * FROM forge.inventory_reservations "
                            "WHERE organization_id=:org AND id=:id FOR UPDATE"
                        ),
                        {"org": self.ctx.organization_id, "id": reservation_id},
                    )
                )
                .mappings()
                .first()
            )
            if (
                reservation is None
                or reservation["line_id"] != line_id
                or (reservation["warehouse_id"], reservation["product_id"]) != key
            ):
                raise Problem(404, "NOT_FOUND", "占用来源不存在或不属于本次操作")
        try:
            exact(qty, positive=True)
            consume = qty if reservation_id and kind == "ISSUE" else ZERO
            if reservation and kind in ("ISSUE", "RELEASE") and qty > reservation["remaining_qty"]:
                raise InventoryError("INSUFFICIENT_RESERVATION", "可消费或释放的占用不足")
            if kind in ("RECEIVE", "TRANSFER_IN"):
                new = old.receive(qty, cost, value)
            elif kind in ("ISSUE", "TRANSFER_OUT"):
                new = old.issue(qty, consume)
            elif kind == "RESERVE":
                new = old.reserve(qty)
                if reservation is None:
                    reservation_id = (
                        await self.db.execute(
                            text(
                                "INSERT INTO forge.inventory_reservations "
                                "(organization_id,warehouse_id,product_id,document_id,line_id) "
                                "VALUES (:org,:wh,:product,:doc,:line) ON CONFLICT "
                                "(organization_id,warehouse_id,product_id,line_id) DO UPDATE "
                                "SET line_id=EXCLUDED.line_id RETURNING id"
                            ),
                            {
                                "org": self.ctx.organization_id,
                                "wh": key[0],
                                "product": key[1],
                                "doc": source["document_id"],
                                "line": line_id,
                            },
                        )
                    ).scalar_one()
            elif kind == "RELEASE" and reservation is not None:
                new = old.reserve(-qty)
            else:
                raise InventoryError("INVALID_OPERATION", "无效库存操作")
        except InventoryError as exc:
            raise Problem(409, exc.code, exc.detail) from exc
        rd = qty if kind == "RESERVE" else ZERO
        cd = qty if kind == "ISSUE" and reservation_id else ZERO
        ld = qty if kind == "RELEASE" else ZERO
        return await self._append(
            key, source, operation, kind, old, new, reservation_id, rd, cd, ld
        )

    async def _append(
        self,
        key: Key,
        source: dict,
        operation: UUID,
        kind: str,
        old: Balance,
        new: Balance,
        reservation_id: UUID | None = None,
        rd: Decimal = ZERO,
        cd: Decimal = ZERO,
        ld: Decimal = ZERO,
        original: dict | None = None,
        reversal_id: UUID | None = None,
    ) -> dict:
        row = self.rows[key]
        version = row["version"] + 1
        with localcontext() as context:
            context.prec = 50
            tail = (
                (new.value - old.value) + rounded((old.qty - new.qty) * old.cost, 4)
                if (kind in ("ISSUE", "TRANSFER_OUT") and new.qty == 0)
                else ZERO
            )
        params = {
            "org": self.ctx.organization_id,
            "wh": key[0],
            "product": key[1],
            "sequence": version,
            "kind": kind,
            "qty": new.qty - old.qty,
            "reserved": new.reserved - old.reserved,
            "value": new.value - old.value,
            "before": old.cost,
            "after": new.cost,
            "tail": tail,
            "doc": source["document_id"],
            "line": source["id"],
            "operation": operation,
            "reservation": reservation_id,
            "original": original["id"] if original else None,
            "reversal": reversal_id,
            "rd": rd,
            "cd": cd,
            "ld": ld,
            "actor": self.ctx.user_id,
            "rid": self.ctx.request_id,
        }
        movement = dict(
            (
                await self.db.execute(
                    text(
                        "INSERT INTO forge.inventory_movements "
                        "(organization_id,warehouse_id,product_id,"
                        "sequence,kind,base_qty,reserved_qty_delta,"
                        "value_delta,before_avg_cost,after_avg_cost,"
                        "rounding_delta,document_id,line_id,operation_id,"
                        "reservation_id,original_movement_id,"
                        "reversal_id,reservation_reserved_delta,"
                        "reservation_consumed_delta,"
                        "reservation_released_delta,actor_id,request_id) VALUES "
                        "(:org,:wh,:product,:sequence,:kind,:qty,"
                        ":reserved,:value,:before,:after,:tail,:doc,:line,"
                        ":operation,:reservation,:original,:reversal,"
                        ":rd,:cd,:ld,:actor,:rid) RETURNING *"
                    ),
                    params,
                )
            )
            .mappings()
            .one()
        )
        await self.db.execute(
            text(
                "UPDATE forge.inventory_balances SET on_hand_qty=:qty,"
                "reserved_qty=:reserved,inventory_value=:value,"
                "avg_unit_cost=:cost,version=:version "
                "WHERE organization_id=:org AND id=:id"
            ),
            {
                "org": self.ctx.organization_id,
                "id": row["id"],
                "qty": new.qty,
                "reserved": new.reserved,
                "value": new.value,
                "cost": new.cost,
                "version": version,
            },
        )
        if reservation_id:
            await self.db.execute(
                text(
                    "UPDATE forge.inventory_reservations SET "
                    "reserved_qty=reserved_qty+:rd,consumed_qty=consumed_qty+:cd,"
                    "released_qty=released_qty+:ld,remaining_qty=remaining_qty+:rd-:cd-:ld,"
                    "version=version+1 WHERE organization_id=:org AND id=:id"
                ),
                {
                    "org": self.ctx.organization_id,
                    "id": reservation_id,
                    "rd": rd,
                    "cd": cd,
                    "ld": ld,
                },
            )
        self.rows[key].update(
            on_hand_qty=new.qty,
            reserved_qty=new.reserved,
            inventory_value=new.value,
            avg_unit_cost=new.cost,
            version=version,
        )
        await record_mutation(
            self.db,
            self.ctx,
            "inventory.movement.recorded",
            "inventory_movement",
            movement["id"],
            None,
            {"version": version, "document_id": str(source["document_id"])},
        )
        return movement

    async def reverse(self, movement: dict, reversal_id: UUID) -> dict:
        self.ctx.require("inventory.reverse")
        key = (movement["warehouse_id"], movement["product_id"])
        if self.rows[key]["version"] != movement["sequence"]:
            raise Problem(
                409, "REVERSAL_DEPENDENCY_CONFLICT", "该商品仓库已有后续变动，不能冲销此单"
            )
        old = self.state(key)
        try:
            new = Balance(
                old.qty - movement["base_qty"],
                old.reserved - movement["reserved_qty_delta"],
                old.value - movement["value_delta"],
                movement["before_avg_cost"],
            )
        except InventoryError as exc:
            raise Problem(409, exc.code, exc.detail) from exc
        source = await self.source(movement["line_id"], key)
        return await self._append(
            key,
            source,
            reversal_id,
            "REVERSE",
            old,
            new,
            movement["reservation_id"],
            -movement["reservation_reserved_delta"],
            -movement["reservation_consumed_delta"],
            -movement["reservation_released_delta"],
            original=movement,
            reversal_id=reversal_id,
        )

    async def reconcile(self, *, repair: bool = False) -> list[dict]:
        self.ctx.require("inventory.reconcile")
        self.ctx.require("product.cost.read")
        results = []
        for key in sorted(self.rows):
            movements = (
                (
                    await self.db.execute(
                        text(
                            "SELECT * FROM forge.inventory_movements "
                            "WHERE organization_id=:org AND warehouse_id=:wh AND "
                            "product_id=:product "
                            "ORDER BY sequence"
                        ),
                        {"org": self.ctx.organization_id, "wh": key[0], "product": key[1]},
                    )
                )
                .mappings()
                .all()
            )
            qty = reserved = value = cost = ZERO
            reservation_totals: dict[UUID, list[Decimal]] = {}
            for sequence, m in enumerate(movements, 1):
                if sequence != m["sequence"] or cost != m["before_avg_cost"]:
                    raise Problem(409, "LEDGER_INCONSISTENT", "流水序列或成本快照不一致，不能重建")
                prior = Balance(qty, reserved, value, cost)
                try:
                    if m["kind"] in ("RECEIVE", "TRANSFER_IN"):
                        replayed = prior.receive(m["base_qty"], value=m["value_delta"])
                    elif m["kind"] in ("ISSUE", "TRANSFER_OUT"):
                        replayed = prior.issue(-m["base_qty"], consume=-m["reserved_qty_delta"])
                    elif m["kind"] in ("RESERVE", "RELEASE"):
                        replayed = prior.reserve(m["reserved_qty_delta"])
                    else:
                        original = next(
                            (x for x in movements if x["id"] == m["original_movement_id"]), None
                        )
                        if not original or any(
                            m[field] != -original[field]
                            for field in ("base_qty", "reserved_qty_delta", "value_delta")
                        ):
                            raise InventoryError("LEDGER_INCONSISTENT", "冲销流水与原记录不一致")
                        replayed = Balance(
                            qty + m["base_qty"],
                            reserved + m["reserved_qty_delta"],
                            value + m["value_delta"],
                            original["before_avg_cost"],
                        )
                    recorded = Balance(
                        qty + m["base_qty"],
                        reserved + m["reserved_qty_delta"],
                        value + m["value_delta"],
                        m["after_avg_cost"],
                    )
                    if replayed != recorded:
                        raise InventoryError("LEDGER_INCONSISTENT", "流水成本计算与记录不一致")
                except InventoryError as exc:
                    raise Problem(409, "LEDGER_INCONSISTENT", "流水校验失败，不能重建投影") from exc
                qty += m["base_qty"]
                reserved += m["reserved_qty_delta"]
                value += m["value_delta"]
                cost = m["after_avg_cost"]
                Balance(qty, reserved, value, cost)
                if m["reservation_id"]:
                    totals = reservation_totals.setdefault(m["reservation_id"], [ZERO, ZERO, ZERO])
                    for i, field in enumerate(
                        (
                            "reservation_reserved_delta",
                            "reservation_consumed_delta",
                            "reservation_released_delta",
                        )
                    ):
                        totals[i] += m[field]
            expected = {
                "on_hand_qty": qty,
                "reserved_qty": reserved,
                "inventory_value": value,
                "avg_unit_cost": cost,
                "version": len(movements),
            }
            current = self.rows[key]
            differences = {
                k: {"current": str(current[k]), "expected": str(v)}
                for k, v in expected.items()
                if current[k] != v
            }
            reservations = (
                (
                    await self.db.execute(
                        text(
                            "SELECT * FROM forge.inventory_reservations "
                            "WHERE organization_id=:org AND warehouse_id=:wh AND "
                            "product_id=:product FOR UPDATE"
                        ),
                        {"org": self.ctx.organization_id, "wh": key[0], "product": key[1]},
                    )
                )
                .mappings()
                .all()
            )
            for r in reservations:
                a, b, c = reservation_totals.get(r["id"], [ZERO, ZERO, ZERO])
                if (
                    r["reserved_qty"],
                    r["consumed_qty"],
                    r["released_qty"],
                    r["remaining_qty"],
                ) != (a, b, c, a - b - c):
                    differences["reservation:" + str(r["id"])] = {
                        "current": "projection mismatch",
                        "expected": "ledger totals",
                    }
                    if repair:
                        await self.db.execute(
                            text(
                                "UPDATE forge.inventory_reservations SET "
                                "reserved_qty=:a,consumed_qty=:b,"
                                "released_qty=:c,remaining_qty=:remaining,"
                                "version=version+1 WHERE organization_id=:org AND id=:id"
                            ),
                            {
                                "org": self.ctx.organization_id,
                                "id": r["id"],
                                "a": a,
                                "b": b,
                                "c": c,
                                "remaining": a - b - c,
                            },
                        )
            if repair and differences:
                await self.db.execute(
                    text(
                        "UPDATE forge.inventory_balances SET on_hand_qty=:on_hand_qty,"
                        "reserved_qty=:reserved_qty,"
                        "inventory_value=:inventory_value,avg_unit_cost=:avg_unit_cost,"
                        "version=:version WHERE organization_id=:org AND id=:id"
                    ),
                    expected | {"org": self.ctx.organization_id, "id": current["id"]},
                )
                await record_mutation(
                    self.db,
                    self.ctx,
                    "inventory.projection.rebuilt",
                    "inventory_balance",
                    current["id"],
                    json.loads(json.dumps(differences)),
                    {"version": expected["version"]},
                )
            results.append(
                {
                    "warehouse_id": str(key[0]),
                    "product_id": str(key[1]),
                    "differences": differences,
                    "sequence": len(movements),
                    "repaired": bool(repair and differences),
                }
            )
        return results
