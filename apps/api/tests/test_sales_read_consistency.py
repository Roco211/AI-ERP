"""Sales status-filter pages revalidate rows after a concurrent business commit."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_catalog import catalog_client as catalog_client
from test_sales_orders import action, opening, so
from test_sales_orders import sale as sale
from test_sales_shipments import post_shipment, shipment


@pytest.mark.parametrize("resource", ["orders", "documents"])
async def test_status_filtered_sales_page_omits_rows_changed_before_read_lock(
    catalog_client, sale, monkeypatch, resource
):
    c = catalog_client
    await opening(c, sale)
    order = await so(c, sale)
    confirmed = await action(c, order)
    assert confirmed.status_code == 200, confirmed.text
    order = confirmed.json()
    row, _ = await shipment(c, order)
    status = "CONFIRMED" if resource == "orders" else "DRAFT"
    query_marker = (
        "SELECT id FROM forge.sales_orders WHERE"
        if resource == "orders"
        else "SELECT d.id,sd.order_id FROM forge.sales_documents"
    )
    original = AsyncSession.execute
    intervened = False

    async def commit_between_selection_and_lock(self, statement, *args, **kwargs):
        nonlocal intervened
        result = await original(self, statement, *args, **kwargs)
        sql = " ".join(str(statement).split())
        if query_marker in sql and "LIMIT" in sql and not intervened:
            # The outer request selected the old status but holds no order lock yet.
            # Complete a separate API transaction before it acquires its read lock.
            intervened = True
            response = (
                await action(c, order, "close", "并发关闭释放剩余占用")
                if resource == "orders"
                else await post_shipment(c, row)
            )
            assert response.status_code == 200, response.text
        return result

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", commit_between_selection_and_lock)
        response = await c.get("/api/v1/sales/" + resource, params={"status": status})

    assert intervened, "The concurrent commit must happen after the page selected its IDs"
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["items"] == []
    assert page["total"] == 0
    # The intervening write really committed, rather than merely filtering an unchanged row.
    changed = await c.get(
        "/api/v1/sales/" + resource + "/" + (order if resource == "orders" else row)["id"]
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["status"] == ("CLOSED" if resource == "orders" else "POSTED")
