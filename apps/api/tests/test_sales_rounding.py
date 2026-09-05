"""Each shipment is a commercial rounding boundary; returns clear its own frozen totals."""

from decimal import Decimal as D

from test_catalog import catalog_client as catalog_client
from test_purchasing import action as purchase_action
from test_sales_margin_history import amounts, return_draft
from test_sales_orders import action, balance, detail, so
from test_sales_orders import sale as sale
from test_sales_shipments import post_shipment, restock, shipment, stock_document


async def test_split_shipment_sales_rounding_and_each_original_full_return(catalog_client, sale):
    c = catalog_client
    sale = sale | {"lines": [sale["lines"][0] | {"qty": "3", "unit_price": "0.00005"}]}
    purchase = await restock(c, sale, "3", "0.333333")
    assert (await purchase_action(c, purchase, "post", document=True)).status_code == 200
    order = await so(c, sale)
    amounts(await detail(c, order), amount="0.0002")
    assert (await action(c, order)).status_code == 200

    issued = []
    costs = ("0.3333", "0.3333", "0.3334")
    for expected_cost in costs:
        document, _ = await shipment(c, order, "1")
        assert (await post_shipment(c, document)).status_code == 200
        captured = await stock_document(c, document)
        amounts(captured, amount="0.0001", actual_cost=expected_cost)
        amounts(captured["lines"][0], qty=1, unit_price="0.00005", amount="0.0001")
        issued.append(document)

    # Inventory consumes its full remaining value, while each shipment keeps round(qty*price,4).
    stock = (await balance(c, sale))[0]
    assert all(D(stock[field]) == 0 for field in ("on_hand_qty", "reserved_qty", "inventory_value"))
    fully_shipped = await detail(c, order)
    assert fully_shipped["fulfillment_status"] == "FULFILLED"
    amounts(
        fully_shipped,
        amount="0.0002",
        shipment_amount="0.0003",
        shipment_cost="1.0000",
        net_sales_amount="0.0003",
        net_cost="1.0000",
        gross_margin="-0.9997",
    )

    for document, expected_cost in zip(issued, costs, strict=True):
        returned = await return_draft(c, document, "1")
        draft = await stock_document(c, returned)
        amounts(draft["lines"][0], amount="0.0001", return_cost=expected_cost)
        assert (await post_shipment(c, returned)).status_code == 200
        amounts(await stock_document(c, returned), amount="0.0001", actual_cost=expected_cost)
        source = (await stock_document(c, document))["lines"][0]
        amounts(source, returned_qty=1, returnable_qty=0, returned_amount="0.0001")

    final = await detail(c, order)
    amounts(
        final,
        amount="0.0002",
        shipment_amount="0.0003",
        shipment_cost=1,
        return_amount="0.0003",
        return_cost=1,
        net_sales_amount=0,
        net_cost=0,
        gross_margin=0,
    )
    assert final["fulfillment_status"] == "FULFILLED" and final["status"] == "CONFIRMED"
    amounts(final["lines"][0], remaining_qty=0, executable_qty=0, returned_base_qty=3)
    stock = (await balance(c, sale))[0]
    amounts(stock, on_hand_qty=3, reserved_qty=0, inventory_value=1)
