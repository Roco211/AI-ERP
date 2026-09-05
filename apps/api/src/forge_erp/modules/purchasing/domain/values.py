from decimal import Decimal, localcontext

from forge_erp.modules.inventory.domain.values import InventoryError, exact, rounded


def line_amount(qty: Decimal, price: Decimal) -> Decimal:
    exact(qty, positive=True)
    if exact(price) < 0:
        raise InventoryError("INVALID_NUMBER", "采购单价不能为负数")
    with localcontext() as ctx:
        ctx.prec = 50
        return rounded(qty * price, 4)


def return_amount(qty: Decimal, price: Decimal, remaining_qty: Decimal, remaining_value: Decimal):
    if qty > remaining_qty:
        raise InventoryError("OVER_RETURN", "退货超过原收货剩余可退数量")
    amount = remaining_value if qty == remaining_qty else line_amount(qty, price)
    if amount < 0 or amount > remaining_value:
        raise InventoryError("PURCHASE_PRICE_PRECISION_CONFLICT", "退货金额精度冲突，请核对数量")
    return exact(amount, 4)
