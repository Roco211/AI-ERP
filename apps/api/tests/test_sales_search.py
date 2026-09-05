"""Literal sales search must not interpret user text as LIKE escapes or wildcards."""

import pytest
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_sales_orders import action, opening, so
from test_sales_orders import sale as sale
from test_sales_shipments import shipment


@pytest.mark.parametrize(
    ("literal", "decoy_name"),
    [("\\", "甲乙五金"), ("%", "甲中间乙五金"), ("_", "甲X乙五金")],
)
async def test_order_and_document_search_matches_literal_customer_text(
    catalog_client, sale, identities, literal, decoy_name
):
    c = catalog_client
    query = "甲" + literal + "乙"
    target_customer = await create(
        c, "customers", {"code": "SEARCH-TARGET", "name": query + "五金"}
    )
    decoy_customer = await create(c, "customers", {"code": "SEARCH-DECOY", "name": decoy_name})
    assert target_customer.status_code == decoy_customer.status_code == 201
    target = await so(c, sale | {"customer_id": target_customer.json()["id"]})
    decoy = await so(c, sale | {"customer_id": decoy_customer.json()["id"]})
    await so(c, sale)
    await opening(c, sale, "20")
    assert (await action(c, target)).status_code == 200
    assert (await action(c, decoy)).status_code == 200
    target_document, _ = await shipment(c, target, "1")
    await shipment(c, decoy, "1")

    for route, expected in [("orders", target), ("documents", target_document)]:
        response = await c.get("/api/v1/sales/" + route, params={"q": query, "page_size": 1})
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["total"] == 1
        assert [row["id"] for row in page["items"]] == [expected["id"]]
        assert page["items"][0]["customer_name"] == query + "五金"
        following = await c.get(
            "/api/v1/sales/" + route, params={"q": query, "page_size": 1, "page": 2}
        )
        assert following.status_code == 200, following.text
        assert following.json()["total"] == 1 and following.json()["items"] == []

    login = await create(
        c,
        "auth/login",
        {
            "organization_code": identities[1]["code"],
            "email": "same@example.test",
            "password": "test-only-password-8472",
        },
    )
    assert login.status_code == 200, login.text
    for route in ("orders", "documents"):
        response = await c.get("/api/v1/sales/" + route, params={"q": query})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 0 and response.json()["items"] == []
