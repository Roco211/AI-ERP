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
