"""Versioned fixed-response evals. They prove guards, not a live model's ability."""

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from test_assistant_provider_settings import save
from test_assistant_runtime import assistant as assistant
from test_assistant_runtime import decisions, message
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_sales_orders import sale as sale

from forge_erp.modules.assistant.application import runtime

CASES = json.loads((Path(__file__).parents[1] / "evals/assistant-v0.11.json").read_text())
CHAT_CASES = json.loads((Path(__file__).parents[1] / "evals/assistant-chat.json").read_text())


@pytest.mark.parametrize("case", CHAT_CASES, ids=lambda case: case["id"])
async def test_fixed_chat_safety_eval(assistant, monkeypatch, case):
    """Controlled outputs prove routing/output guards, not live model classification."""
    client, _, conversation = assistant
    decisions(monkeypatch, [{"action": "chat"}])
    calls = 0

    async def chat(messages, connection):
        nonlocal calls
        calls += 1
        for chunk in case["reply"]:
            yield chunk

    monkeypatch.setattr(runtime, "stream_chat", chat)
    response = await client.post(
        f"/api/v1/ai/conversations/{conversation}/messages",
        json={"message": case["question"]},
        headers={"Idempotency-Key": uuid4().hex, "Accept": "text/event-stream"},
    )
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    final = events[-1]["turn"]
    assert final["state"] == case["expected"]
    assert final["interaction"] == case["interaction"]
    assert final["tool_calls"] == 0 and final["proposal"] is None and final["evidence"] == []
    assert calls == case["chat_calls"]
    if case["expected"] == "FAILED" or not calls:
        assert not any(event["type"] == "delta" for event in events)
        assert "999999" not in response.text and "private hidden reasoning" not in response.text
    else:
        assert final["answer"] == "".join(case["reply"])


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
async def test_fixed_response_eval(assistant, monkeypatch, case):
    client, _, conversation = assistant
    values = [case["decision"]]
    if case["id"] == "inventory":
        values.append({"action": "answer", "code": "results", "evidence_ids": ["e1"]})
    decisions(monkeypatch, values)
    result = await message(client, conversation, text=case["question"])
    assert result.status_code == 200, result.text
    assert result.json()["state"] == case["expected"], result.json()
    assert result.json()["proposal"] is None


@pytest.mark.parametrize("kind", ["SALES", "PURCHASE"])
async def test_real_graph_draft_review_receipt_survives_checkpoint_failure(
    catalog_client, sale, monkeypatch, kind
):
    client = catalog_client
    order = dict(sale)
    if kind == "PURCHASE":
        supplier = await create(client, "suppliers", {"code": "SP", "name": "采购供货商"})
        assert supplier.status_code == 201, supplier.text
        order.pop("customer_id")
        order["supplier_id"] = supplier.json()["id"]
        order["lines"] = [
            {k: v for k, v in line.items() if k != "pricing_mode"} for line in order["lines"]
        ]
    product = (await client.get("/api/v1/products/" + order["lines"][0]["product_id"])).json()
    unit = (await client.get("/api/v1/units/" + order["lines"][0]["unit_id"])).json()
    assert (await save(client)).status_code == 200
    conv = await create(client, "ai/conversations", {"title": "开单评估"})
    assert conv.status_code == 200, conv.text
    queries = [
        {
            "tool": "search_customers" if kind == "SALES" else "search_suppliers",
            "arguments": {"q": "SC" if kind == "SALES" else "SP"},
        },
        {"tool": "search_warehouses", "arguments": {"q": "SW"}},
        {"tool": "search_products", "arguments": {"q": product["sku"]}},
        {"tool": "search_units", "arguments": {"q": unit["name"]}},
    ]
    calls = decisions(
        monkeypatch,
        [
            {"action": "queries", "queries": queries},
            {"action": "draft", "draft": {"kind": kind, "order": order}},
        ],
    )
    prompt = (
        f"为{'SC' if kind == 'SALES' else 'SP'}从SW仓库开单，"
        f"商品{product['sku']}，数量7{unit['name']}，单价15元。"
    )
    result = await message(client, conv.json()["id"], text=prompt)
    assert result.status_code == 200, result.text
    turn = result.json()
    assert turn["state"] == "WAITING", turn
    proposal = turn["proposal"]
    assert Decimal(proposal["preview"]["total_amount"]) == Decimal("105.00")
    assert len(calls) == 2

    def unavailable_checkpoint(self):
        raise RuntimeError("synthetic checkpoint outage after business commit")

    monkeypatch.setattr(runtime.Run, "graph", unavailable_checkpoint)
    body = {
        "expected_revision": proposal["revision"],
        "confirmation_hash": proposal["preview"]["confirmation_hash"],
    }
    key = uuid4().hex
    receipt = await create(client, f"ai/proposals/{proposal['id']}/approve", body, key)
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["status"] == "DRAFT"
    replay = await create(client, f"ai/proposals/{proposal['id']}/approve", body, uuid4().hex)
    assert replay.json() == receipt.json()
    endpoint = "sales" if kind == "SALES" else "purchasing"
    orders = (await client.get(f"/api/v1/{endpoint}/orders")).json()
    assert orders["total"] == 1 and orders["items"][0]["status"] == "DRAFT"
    assert (await client.get("/api/v1/inventory/movements")).json()["items"] == []
    history = (await client.get(f"/api/v1/ai/conversations/{conv.json()['id']}")).json()
    assert history["turns"][0]["state"] == "COMPLETED"
    assert history["turns"][0]["proposal"]["receipt"] == receipt.json()


async def test_rejection_commits_before_checkpoint_continuation(catalog_client, sale, monkeypatch):
    from test_assistant_state import proposed

    from forge_erp.core.db import sessions
    from forge_erp.modules.assistant.application import state

    _, _, proposal = await proposed(catalog_client, sale)
    observed = []

    async def resume(token, rid, turn_id):
        async with sessions.begin() as db:
            ctx = await state.authorize(db, token, rid)
            saved = await state.proposal_detail(db, ctx, proposal.id)
            turn = await state.read_turn(db, ctx, turn_id)
            observed.append((saved.status, turn.state))

    monkeypatch.setattr(runtime, "resume_review", resume)
    response = await create(
        catalog_client,
        f"ai/proposals/{proposal.id}/reject",
        {"expected_revision": proposal.revision},
    )
    assert response.status_code == 200, response.text
    assert observed == [("REJECTED", "COMPLETED")]
