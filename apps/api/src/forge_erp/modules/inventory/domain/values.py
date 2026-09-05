"""Deterministic inventory arithmetic; no framework or persistence dependencies."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

ZERO = Decimal(0)


class InventoryError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(detail)


def exact(value: Decimal, places: int = 6, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InventoryError("INVALID_NUMBER", "请使用有效的十进制数值")
    with localcontext() as ctx:
        ctx.prec = 50
        if abs(value) >= Decimal(10) ** (20 - places):
            raise InventoryError("NUMERIC_OVERFLOW", "数值超出可保存范围")
        rounded = value.quantize(Decimal(1).scaleb(-places))
    if rounded != value or (positive and value <= 0):
        raise InventoryError("INVALID_NUMBER", "数值精度或范围不正确")
    return rounded


def rounded(value: Decimal, places: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 50
        return exact(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), places)


@dataclass(frozen=True)
class Balance:
    qty: Decimal = ZERO
    reserved: Decimal = ZERO
    value: Decimal = ZERO
    cost: Decimal = ZERO

    def __post_init__(self):
        for v in (self.qty, self.reserved, self.cost):
            exact(v)
        exact(self.value, 4)
        if self.qty < 0 or self.reserved < 0 or self.reserved > self.qty:
            raise InventoryError("INSUFFICIENT_STOCK", "库存或可用库存不足")
        if self.value < 0 or self.cost < 0 or (self.qty == 0 and self.value != 0):
            raise InventoryError("COST_PRECISION_CONFLICT", "库存金额与数量不一致，请核对成本")

    @property
    def available(self) -> Decimal:
        return self.qty - self.reserved

    def receive(
        self, qty: Decimal, cost: Decimal | None = None, value: Decimal | None = None
    ) -> Balance:
        exact(qty, positive=True)
        if value is None:
            if cost is None or exact(cost) < 0:
                raise InventoryError("COST_REQUIRED", "请明确填写基本单位成本")
            with localcontext() as ctx:
                ctx.prec = 50
                value = rounded(qty * cost, 4)
        exact(value, 4)
        if value < 0:
            raise InventoryError("INVALID_NUMBER", "入库金额不能为负数")
        with localcontext() as ctx:
            ctx.prec = 50
            total = self.value + value
            return Balance(
                self.qty + qty, self.reserved, total, rounded(total / (self.qty + qty), 6)
            )

    def issue(self, qty: Decimal, consume: Decimal = ZERO) -> Balance:
        exact(qty, positive=True)
        exact(consume)
        if consume < 0 or consume > qty or consume > self.reserved:
            raise InventoryError("INSUFFICIENT_RESERVATION", "可消费的占用不足")
        if qty - consume > self.available:
            raise InventoryError("INSUFFICIENT_STOCK", "可用库存不足")
        with localcontext() as ctx:
            ctx.prec = 50
            amount = self.value if qty == self.qty else rounded(qty * self.cost, 4)
            return Balance(self.qty - qty, self.reserved - consume, self.value - amount, self.cost)

    def reserve(self, delta: Decimal) -> Balance:
        exact(delta)
        if delta == 0:
            raise InventoryError("INVALID_NUMBER", "占用变动不能为零")
        return Balance(self.qty, self.reserved + delta, self.value, self.cost)
