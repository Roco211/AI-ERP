"""Server-authoritative commercial arithmetic, independent of web/persistence."""

from decimal import Decimal, localcontext

from forge_erp.modules.inventory.domain.values import InventoryError, exact, rounded


def line_amount(qty: Decimal, price: Decimal) -> Decimal:
    exact(qty, positive=True)
    if exact(price) < 0:
        raise InventoryError("INVALID_NUMBER", "销售单价不能为负数")
    with localcontext() as ctx:
        ctx.prec = 50
        return rounded(qty * price, 4)


def converted_price(price: Decimal, target: Decimal, original: Decimal = Decimal(1)) -> Decimal:
    exact(price)
    exact(target, positive=True)
    exact(original, positive=True)
    if price < 0:
        raise InventoryError("INVALID_NUMBER", "销售单价不能为负数")
    with localcontext() as ctx:
        ctx.prec = 50
        result = rounded(price * target / original, 6)
    if price > 0 and result == 0:
        raise InventoryError("PRICE_PRECISION_CONFLICT", "换算报价低于可保存精度，请核对销售单位")
    return result


def return_values(
    qty: Decimal,
    price: Decimal,
    original_qty: Decimal,
    original_amount: Decimal,
    original_cost: Decimal,
    returned_qty: Decimal,
    returned_amount: Decimal,
    returned_cost: Decimal,
) -> tuple[Decimal, Decimal]:
    """Allocate two independent original totals; never round an intermediate unit cost."""
    exact(qty, positive=True)
    exact(original_qty, positive=True)
    for number in (price, returned_qty):
        if exact(number) < 0:
            raise InventoryError("INVALID_NUMBER", "原单价格和已退数量不能为负数")
    for number in (original_amount, original_cost, returned_amount, returned_cost):
        if exact(number, 4) < 0:
            raise InventoryError("INVALID_NUMBER", "原单和已退金额不能为负数")
    with localcontext() as ctx:
        ctx.prec = 50
        remaining_qty = original_qty - returned_qty
        remaining_amount = original_amount - returned_amount
        remaining_cost = original_cost - returned_cost
        if qty > remaining_qty:
            raise InventoryError("OVER_RETURN", "退货超过原出库剩余可退数量")
        amount = remaining_amount if qty == remaining_qty else line_amount(qty, price)
        cost = (
            remaining_cost
            if qty == remaining_qty
            else rounded(original_cost * qty / original_qty, 4)
        )
        if not (0 <= amount <= remaining_amount and 0 <= cost <= remaining_cost):
            raise InventoryError("RETURN_PRECISION_CONFLICT", "退货金额精度冲突，请核对数量")
        return exact(amount, 4), exact(cost, 4)
