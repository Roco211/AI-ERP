from datetime import UTC, datetime
from decimal import Decimal as D

import pytest

from forge_erp.modules.inventory.domain.values import InventoryError
from forge_erp.modules.replenishment.domain.values import calculate, sales_window


@pytest.mark.parametrize(
    "available,inbound,shipped,returned,safety,batch,lead,expected",
    [
        (8, 6, 90, 30, 10, 20, 5, (60, 2, 20, 34, 14, 20, 20, 4)),
        (8, 30, 90, 30, 10, 20, 5, (60, 2, 20, 34, 38, 0, 0, 4)),
        (21, 0, 90, 30, 10, 20, 5, (60, 2, 20, 34, 21, 13, 0, "10.5")),
        (3, 2, 0, 0, 10, 20, 5, (0, 0, 10, 10, 5, 5, 20, None)),
        (0, 0, 0, 5, 0, 100, 5, (-5, 0, 0, 0, 0, 0, 0, None)),
        (8, 6, 90, 30, 10, 20, None, (60, 2, 10, 10, 14, 0, 0, 4)),
    ],
)
def test_spec_examples(available, inbound, shipped, returned, safety, batch, lead, expected):
    result = calculate(*map(D, (available, inbound, shipped, returned, safety, batch)), lead)
    keys = (
        "net_sales_qty",
        "daily_sales_qty",
        "reorder_point",
        "target_stock",
        "inventory_position",
        "gap",
        "suggested_base_qty",
        "days_of_stock",
    )
    assert [result[key] for key in keys] == [None if v is None else D(v) for v in expected]


def test_repeating_daily_demand_rounds_only_final_quantity_and_inclusive_threshold():
    result = calculate(D(0), D(0), D(1), D(0), D(0), D(0), 1)
    assert result["suggested_base_qty"] == D("0.266667")
    assert result["daily_sales_qty"] > D("0.033333")
    boundary = calculate(D(20), D(0), D(90), D(30), D(10), D(0), 5)
    assert boundary["candidate"] is True and boundary["suggested_base_qty"] == 14
    # Explicit zero lead still has a seven-day dynamic target, unlike missing lead.
    zero = calculate(D(0), D(0), D(30), D(0), D(0), D(0), 0)
    assert zero["suggested_base_qty"] == 7 and "MISSING_LEAD_DAYS" not in zero["reasons"]
    missing = calculate(D(0), D(0), D(30), D(0), D(0), D(0), None)
    assert missing["suggested_base_qty"] == 0 and "MISSING_LEAD_DAYS" in missing["reasons"]


def test_tiny_gap_ceil_and_overflow_are_not_silently_rounded_or_capped():
    result = calculate(D(0), D(0), D("0.000001"), D(0), D(0), D(0), 0)
    assert result["suggested_base_qty"] == D("0.000001")
    with pytest.raises(InventoryError, match="超出"):
        calculate(D(0), D(0), D("99999999999999"), D(0), D(0), D(0), 3650)


@pytest.mark.parametrize("invalid", [0.1, D("NaN"), D("Infinity"), D(-1)])
def test_domain_refuses_floats_nonfinite_and_negative_fact_quantities(invalid):
    with pytest.raises(InventoryError):
        calculate(invalid, D(0), D(0), D(0), D(0), D(0), 0)


def test_window_uses_complete_business_days_including_dst():
    start, end = sales_window(datetime(2026, 9, 6, 15, 59, tzinfo=UTC), "Asia/Shanghai")
    assert start == datetime(2026, 8, 6, 16, tzinfo=UTC)
    assert end == datetime(2026, 9, 5, 16, tzinfo=UTC)
    start, end = sales_window(datetime(2026, 3, 10, 15, tzinfo=UTC), "America/New_York")
    assert end == datetime(2026, 3, 10, 4, tzinfo=UTC)
    assert start == datetime(2026, 2, 8, 5, tzinfo=UTC)
    assert (end - start).total_seconds() == 30 * 86400 - 3600
