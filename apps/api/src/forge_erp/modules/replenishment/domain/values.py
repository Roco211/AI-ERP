"""Exact rational recommendation arithmetic; only the final quantity is rounded up."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from zoneinfo import ZoneInfo

from forge_erp.modules.inventory.domain.values import InventoryError, exact

ALGORITHM_VERSION = "replenishment-v0.10.1"
ZERO = Decimal(0)


def sales_window(as_of: datetime, timezone: str) -> tuple[datetime, datetime]:
    if as_of.tzinfo is None:
        raise ValueError("as_of must carry a timezone")
    zone = ZoneInfo(timezone)
    today = as_of.astimezone(zone).date()
    end = datetime.combine(today, time.min, zone)
    start = datetime.combine(today - timedelta(days=30), time.min, zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def calculate(
    available: Decimal,
    inbound: Decimal,
    shipped: Decimal,
    returned: Decimal,
    safety: Decimal,
    batch: Decimal,
    lead_days: int | None,
) -> dict:
    for value in (available, inbound, shipped, returned, safety, batch):
        if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
            raise InventoryError("INVALID_NUMBER", "补货依据必须是有限且非负的十进制数量")
    if lead_days is not None and (type(lead_days) is not int or not 0 <= lead_days <= 3650):
        raise InventoryError("INVALID_NUMBER", "供货提前期必须是0至3650的整数日")
    with localcontext() as ctx:
        ctx.prec = 50
        net = shipped - returned
        positive = max(net, ZERO)
        # Numerators in thirtieths keep all comparisons and final ceil exact.
        point_n = safety * 30
        target_n = safety * 30
        if positive > 0 and lead_days is not None:
            point_n += positive * lead_days
            target_n += positive * (lead_days + 7)
        position = available + inbound
        gap_n = max(ZERO, target_n - position * 30)
        candidate = available * 30 <= point_n
        qty = ZERO
        if candidate and gap_n > 0:
            scaled = Fraction(max(batch * 30, gap_n)) * 1_000_000 / 30
            qty = exact(Decimal(-(-scaled.numerator // scaled.denominator)) / 1_000_000)
        reasons = []
        if shipped == 0 and returned == 0:
            reasons.append("NO_SALES_HISTORY")
        elif net <= 0:
            reasons.append("NON_POSITIVE_NET_SALES")
        if lead_days is None:
            reasons.append("MISSING_LEAD_DAYS")
        if not candidate:
            reasons.append("ABOVE_REORDER_POINT")
        elif gap_n == 0:
            reasons.append("INBOUND_COVERS_TARGET" if inbound > 0 else "TARGET_COVERED")
        if safety == 0 and positive == 0:
            reasons.append("NO_DEMAND_BASIS")
        if qty > 0:
            reasons.append("REPLENISHMENT_SUGGESTED")
        return {
            "net_sales_qty": net,
            "daily_sales_qty": positive / 30,
            "reorder_point": point_n / 30,
            "target_stock": target_n / 30,
            "inventory_position": position,
            "gap": gap_n / 30,
            "candidate": candidate,
            "suggested_base_qty": qty,
            "days_of_stock": available * 30 / positive if positive > 0 else None,
            "reasons": reasons,
        }
