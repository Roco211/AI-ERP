"""Deterministic evidence for model-selected identities and per-line numeric roles."""

import subprocess
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from forge_erp.core.errors import Problem
from forge_erp.modules.assistant.application import runtime
from forge_erp.modules.assistant.domain.decisions import DraftDecision

PRODUCT, OTHER, EACH, BOX, CUSTOMER, SUPPLIER, WAREHOUSE = [
    str(UUID(int=value)) for value in range(1, 8)
]


def results():
    return [
        (
            "search_products",
            {
                "total": 2,
                "items": [
                    {"id": PRODUCT, "sku": "AI-BOLT", "name": "螺丝"},
                    {"id": OTHER, "sku": "AI-NUT", "name": "螺母"},
                ],
            },
        ),
        (
            "search_units",
            {
                "total": 2,
                "items": [
                    {"id": EACH, "code": "EA", "name": "个"},
                    {"id": BOX, "code": "BOX", "name": "箱"},
                ],
            },
        ),
        ("search_customers", {"items": [{"id": CUSTOMER, "code": "AI-C", "name": "客户"}]}),
        ("search_suppliers", {"items": [{"id": SUPPLIER, "code": "AI-S", "name": "供应商"}]}),
        ("search_warehouses", {"items": [{"id": WAREHOUSE, "code": "AI-W", "name": "仓库"}]}),
    ]


def prepared(prompt, *, selected=(), query_results=None):
    query_results = query_results or results()
    entities = {}
    for name, payload in query_results:
        runtime._merge_entities(entities, runtime.candidate_entities(name, payload))
    selected = [CUSTOMER, SUPPLIER, WAREHOUSE, *selected]
    resolved = set()
    for name, payload in query_results:
        resolved |= runtime.resolved_candidates(name, payload, prompt, selected, entities)
    return {
        "prompt": prompt,
        "entities": entities,
        "selected_ids": selected,
        "resolved_ids": sorted(resolved),
    }


def draft(qty="2", price="5", *, kind="PURCHASE", lines=None, unit=EACH):
    line = {"product_id": PRODUCT, "unit_id": unit, "qty": qty}
    if kind == "SALES":
        line["pricing_mode"] = "AUTO" if price is None else "MANUAL"
    if price is not None:
        line["unit_price"] = price
    return DraftDecision.model_validate(
        {
            "action": "draft",
            "draft": {
                "kind": kind,
                "order": {
                    "customer_id" if kind == "SALES" else "supplier_id": (
                        CUSTOMER if kind == "SALES" else SUPPLIER
                    ),
                    "warehouse_id": WAREHOUSE,
                    "reason": "用户开单草稿",
                    "lines": lines or [line],
                },
            },
        }
    )


def rejected(decision, data, code):
    with pytest.raises(Problem) as caught:
        runtime.validate_draft_resolution(decision, data)
    assert caught.value.code == code


@pytest.mark.parametrize("tool", ["search_units", "get_product_units"])
def test_single_unit_candidate_never_implies_user_unit_selection(tool):
    row = (
        {"unit_id": BOX, "unit_code": "BOX", "unit_name": "箱"}
        if tool == "get_product_units"
        else {"id": BOX, "code": "BOX", "name": "箱"}
    )
    payload = {"total": 1, "items": [row]}
    assert not runtime.resolved_candidates(tool, payload, "开单3个", [])
    assert runtime.resolved_candidates(tool, payload, "开单3箱", []) == {BOX}
    assert runtime.resolved_candidates(tool, payload, "开单3 BOX", []) == {BOX}
    assert runtime.resolved_candidates(tool, payload, "数量3", [BOX]) == {BOX}
    assert runtime.resolved_candidates(tool, payload, f"数量3，单位{BOX}", []) == {BOX}


def test_selected_ids_must_have_appeared_in_an_authorized_query():
    assert not runtime.resolved_candidates("search_products", {"items": []}, "数量2", [PRODUCT])
    assert not runtime.resolved_candidates("get_inventory", {"id": PRODUCT}, "数量2", [PRODUCT])


def test_longest_name_excludes_nested_shorter_product_and_unit_names():
    products = {
        "items": [
            {"id": PRODUCT, "sku": "P1", "name": "螺丝"},
            {"id": OTHER, "sku": "P2", "name": "自攻螺丝"},
        ]
    }
    assert runtime.resolved_candidates("search_products", products, "自攻螺丝7个", []) == {OTHER}
    units = {"items": [{"id": EACH, "name": "个"}, {"id": BOX, "name": "整箱个"}]}
    assert runtime.resolved_candidates("search_units", units, "自攻螺丝7整箱个", []) == {BOX}


def test_later_detail_cannot_launder_a_nested_shorter_candidate():
    query = results()[0][1]
    query["items"][1]["name"] = "自攻螺丝"
    known = runtime.candidate_entities("search_products", query)
    assert not runtime.resolved_candidates(
        "get_product", query["items"][0], "自攻螺丝7个", [], known
    )


@pytest.mark.parametrize("prompt", ["M80数量7个", "M8-100数量7个", "XM8数量7个", "M8.5数量7个"])
def test_ascii_code_match_does_not_truncate_a_larger_identifier(prompt):
    payload = {"items": [{"id": PRODUCT, "sku": "M8", "name": "螺丝"}]}
    assert not runtime.resolved_candidates("search_products", payload, prompt, [])


def test_casefolded_duplicate_name_and_incomplete_page_cannot_choose_a_product():
    payload = {
        "items": [{"id": PRODUCT, "name": "Bolt"}, {"id": OTHER, "name": "BOLT"}],
        "total": 2,
    }
    assert not runtime.resolved_candidates("search_products", payload, "bolt 数量7个", [])
    payload["items"] = [{"id": PRODUCT, "name": "Bolt", "sku": "P1"}]
    assert not runtime.resolved_candidates("search_products", payload, "Bolt 数量7个", [])
    assert runtime.resolved_candidates("search_products", payload, "P1数量7个", []) == {PRODUCT}


def test_maximal_shared_short_labels_remain_ambiguous_without_expanding_matches():
    entities = {
        str(UUID(int=value)): {"kind": "unit", "codes": [], "names": ["个"]}
        for value in range(1, 201)
    }
    assert runtime._mentions("个" * 4000, entities) == []


def test_same_label_cannot_resolve_a_customer_and_warehouse_without_independent_identity():
    query_results = results()
    query_results[2][1]["items"][0]["name"] = "同名"
    query_results[4][1]["items"][0]["name"] = "同名"
    entities = {}
    for name, payload in query_results:
        runtime._merge_entities(entities, runtime.candidate_entities(name, payload))
    prompt = "为客户同名开销售单，螺丝数量2个，单价5元"
    for name, payload in (query_results[2], query_results[4]):
        assert not runtime.resolved_candidates(name, payload, prompt, [], entities)
    customer_tool, customer_payload = query_results[2]
    warehouse_tool, warehouse_payload = query_results[4]
    assert runtime.resolved_candidates(
        customer_tool, customer_payload, prompt, [CUSTOMER], entities
    ) == {CUSTOMER}
    assert not runtime.resolved_candidates(
        warehouse_tool, warehouse_payload, prompt, [CUSTOMER], entities
    )
    explicit = prompt + "，客户AI-C，仓库AI-W"
    assert runtime.resolved_candidates(customer_tool, customer_payload, explicit, [], entities) == {
        CUSTOMER
    }
    assert runtime.resolved_candidates(
        warehouse_tool, warehouse_payload, explicit, [], entities
    ) == {WAREHOUSE}


@pytest.mark.parametrize("text", ["负2", "负 2", "负数2", "负的2", "负数为2", "负数：2"])
def test_chinese_negative_expression_is_never_read_as_positive_quantity_or_price(text):
    rejected(
        draft("2", "5"),
        prepared(f"商品AI-BOLT数量{text}个，单价5元"),
        "AI_QUANTITY_UNRESOLVED",
    )
    rejected(
        draft("2", "5"),
        prepared(f"商品AI-BOLT数量2个，单价{text.replace('2', '5')}元"),
        "AI_PRICE_UNRESOLVED",
    )


@pytest.mark.parametrize(
    ("role", "prefix", "code"),
    [
        ("数量", "数量", "AI_QUANTITY_UNRESOLVED"),
        ("单价", "商品AI-BOLT数量2个，单价", "AI_PRICE_UNRESOLVED"),
    ],
)
def test_missing_number_after_maximum_whitespace_has_a_hard_time_bound(role, prefix, code):
    prompt = prefix + " " * (4000 - len(prefix))
    pattern = rf"{role}{runtime._ROLE_SEPARATOR}(?P<number>{runtime._NUMBER})"
    # A separate minimal interpreter bounds a regression without freezing pytest's
    # event loop; this reproduces the exact production expression and 4000-char input.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import re,sys; assert re.search(sys.argv[1],sys.argv[2]) is None",
            pattern,
            prompt,
        ],
        check=True,
        timeout=3,
        capture_output=True,
    )
    rejected(draft(), prepared(prompt, selected=[PRODUCT, EACH]), code)


@pytest.mark.parametrize(
    ("prompt", "qty", "price"),
    [
        ("商品AI-BOLT，数量2个，单价5元", "2", "5"),
        ("商品AI-BOLT，2个，单价5元", "2", "5"),
        ("商品AI-BOLT，数量：2，单位个，单价：5元", "2", "5"),
        ("商品AI-BOLT，数量为2个，每个5元", "2", "5"),
        ("商品AI-BOLT，7个，每个15元", "7", "15"),
        ("商品AI-BOLT，数量+2个，单价+5元", "2", "5"),
        ("商品AI-BOLT，数量2.50个，单价￥15.250元", "2.5", "15.25"),
        ("商品AI-BOLT，quantity: 2 EA, unit price: 5", "2", "5"),
        ("商品AI-BOLT，数量2个，单价0元", "2", "0"),
    ],
)
def test_explicit_roles_and_unit_shorthand_are_supported(prompt, qty, price):
    runtime.validate_draft_resolution(draft(qty, price), prepared(prompt))


@pytest.mark.parametrize(
    ("prompt", "qty", "price", "code"),
    [
        ("商品AI-BOLT，数量2个，单价5元", "5", "2", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，数量2个，单价5元", "2", "2", "AI_PRICE_UNRESOLVED"),
        ("商品AI-BOLT，数量-2个，单价5元", "2", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，数量−2个，单价5元", "2", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，数量－2个，单价5元", "2", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，数量–2个，单价5元", "2", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，- 2个，单价5元", "2", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，-\n2个，单价5元", "2", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，数量2个，单价-5元", "2", "5", "AI_PRICE_UNRESOLVED"),
        ("商品AI-BOLT，数量2个，每个− 5元", "2", "5", "AI_PRICE_UNRESOLVED"),
        ("商品AI-BOLT，数量2个，金额5元", "2", "5", "AI_PRICE_UNRESOLVED"),
        ("商品AI-BOLT，数量2个，售价5元", "2", "5", "AI_PRICE_UNRESOLVED"),
        ("商品AI-BOLT，单价5元，单位个", "5", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，型号7，单位个，单价5元", "7", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，日期2026-09-06，单位个，单价5元", "6", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，数量2e3个，单价5元", "2", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，数量3,500个，单价5元", "500", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，数量3 500个，单价5元", "500", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，数量3, 500个，单价5元", "3", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，数量2个，单价3,500元", "2", "3", "AI_PRICE_UNRESOLVED"),
        ("商品AI-BOLT，数量2个，单价3 500元", "2", "3", "AI_PRICE_UNRESOLVED"),
        ("商品AI-BOLT，数量2个，单价1/2元", "2", "1", "AI_PRICE_UNRESOLVED"),
        ("商品AI-BOLT，数量3-5个，单价5元", "3", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，数量3到5个，单价5元", "3", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，数量2万，单位个，单价5元", "2", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，数量2%，单位个，单价5元", "2", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，数量2个或3个，单价5元", "2", "5", "AI_QUANTITY_UNRESOLVED"),
        ("商品AI-BOLT，数量2个，单价5元或者7元", "2", "5", "AI_PRICE_UNRESOLVED"),
    ],
)
def test_numbers_cannot_change_sign_role_or_numeric_notation(prompt, qty, price, code):
    rejected(draft(qty, price), prepared(prompt), code)


def test_wrong_attached_unit_cannot_use_a_unit_mentioned_elsewhere():
    prompt = "商品AI-BOLT，数量3箱，备注还有个，单价5元"
    rejected(draft("3"), prepared(prompt), "AI_QUANTITY_UNRESOLVED")


def test_explicit_selected_unit_can_accompany_role_quantity_without_repeating_label():
    prompt = "商品AI-BOLT，数量2，单价5元"
    runtime.validate_draft_resolution(draft(), prepared(prompt, selected=[EACH]))
    rejected(draft(), prepared(prompt), "AI_ENTITY_UNRESOLVED")


def test_unit_name_inside_longer_product_name_does_not_resolve_a_unit():
    query_results = results()
    query_results[0][1]["items"][0]["name"] = "个性螺丝"
    prompt = "商品个性螺丝，数量2，单价5元"
    rejected(draft(), prepared(prompt, query_results=query_results), "AI_ENTITY_UNRESOLVED")


def test_numeric_text_inside_a_catalog_label_does_not_authorize_quantity():
    query_results = results()
    query_results[0][1]["items"][0]["name"] = "数量2个装螺丝"
    prompt = "商品数量2个装螺丝，单位EA，单价5元"
    rejected(draft(), prepared(prompt, query_results=query_results), "AI_QUANTITY_UNRESOLVED")


def two_lines(first_qty="2", second_qty="3", first_price="5", second_price="7"):
    return draft(
        lines=[
            {"product_id": PRODUCT, "unit_id": EACH, "qty": first_qty, "unit_price": first_price},
            {"product_id": OTHER, "unit_id": EACH, "qty": second_qty, "unit_price": second_price},
        ]
    )


def test_each_product_binds_its_own_quantity_and_price():
    prompt = "商品AI-BOLT螺丝，数量2个，单价5元；商品AI-NUT螺母，数量3个，单价7元"
    runtime.validate_draft_resolution(two_lines(), prepared(prompt))
    rejected(two_lines("3", "2"), prepared(prompt), "AI_QUANTITY_UNRESOLVED")
    rejected(two_lines(first_price="7", second_price="5"), prepared(prompt), "AI_PRICE_UNRESOLVED")


@pytest.mark.parametrize(
    "prompt",
    [
        "数量2个，单价5元；数量3个，单价7元",
        "数量2个商品AI-BOLT，单价5元；数量3个商品AI-NUT，单价7元",
        "商品AI-BOLT数量2个，单价5元；商品AI-NUT；备注数量3个，单价7元；再说AI-BOLT",
    ],
)
def test_unreliable_multiple_line_binding_requires_clarification(prompt):
    rejected(
        two_lines(),
        prepared(prompt, selected=[PRODUCT, OTHER]),
        "AI_QUANTITY_UNRESOLVED",
    )


def test_price_is_not_borrowed_from_another_line():
    prompt = "商品AI-BOLT数量2个，单价5元；商品AI-NUT数量3个"
    rejected(two_lines(second_price="5"), prepared(prompt), "AI_PRICE_UNRESOLVED")


@pytest.mark.parametrize(
    "price", ["单价5元", "每个5元", "单价0元", "单价-5元", "单价5元或者7元", "单价5到7元"]
)
def test_auto_pricing_cannot_silently_ignore_an_explicit_user_price(price):
    prompt = f"商品AI-BOLT，数量2个，{price}"
    rejected(draft("2", None, kind="SALES"), prepared(prompt), "AI_PRICE_UNRESOLVED")


def test_auto_pricing_without_explicit_user_price_remains_available():
    prompt = "商品AI-BOLT，数量2个，使用自动报价"
    runtime.validate_draft_resolution(draft("2", None, kind="SALES"), prepared(prompt))


@pytest.mark.parametrize("manual_product", [PRODUCT, OTHER])
def test_mixed_pricing_modes_are_bound_per_product_line(manual_product):
    auto_product = OTHER if manual_product == PRODUCT else PRODUCT
    decision = draft(
        kind="SALES",
        lines=[
            {
                "product_id": manual_product,
                "unit_id": EACH,
                "qty": "2",
                "pricing_mode": "MANUAL",
                "unit_price": "5",
            },
            {"product_id": auto_product, "unit_id": EACH, "qty": "3", "pricing_mode": "AUTO"},
        ],
    )
    manual_sku = "AI-BOLT" if manual_product == PRODUCT else "AI-NUT"
    auto_sku = "AI-NUT" if manual_product == PRODUCT else "AI-BOLT"
    prompt = f"商品{manual_sku}数量2个，单价5元；商品{auto_sku}数量3个，使用自动报价"
    runtime.validate_draft_resolution(decision, prepared(prompt))
    rejected(decision, prepared(prompt + "，单价7元"), "AI_PRICE_UNRESOLVED")


def test_same_unit_must_be_explicit_for_each_line_or_explicitly_selected():
    prompt = "商品AI-BOLT数量2个，单价5元；商品AI-NUT数量3，单价7元"
    rejected(two_lines(), prepared(prompt), "AI_ENTITY_UNRESOLVED")
    runtime.validate_draft_resolution(two_lines(), prepared(prompt, selected=[EACH]))


@pytest.mark.parametrize("kind", ["SALES", "PURCHASE"])
def test_browser_review_workflow_explicit_prompts(kind):
    query_results = results()
    query_results[1][1]["items"][0].update(code="AI-EACH", name="助手个")
    if kind == "SALES":
        prompt = (
            "为客户 AI-C 从仓库 AI-W 创建销售草稿，商品 AI-BOLT，"
            "数量 3 助手个（AI-EACH），使用自动报价。"
        )
        decision = draft("3", None, kind=kind)
    else:
        prompt = (
            "为供应商 AI-S 向仓库 AI-W 创建采购草稿，商品 AI-BOLT，"
            "数量 2 助手个（AI-EACH），单价 7 元。"
        )
        decision = draft("2", "7", kind=kind)
    runtime.validate_draft_resolution(decision, prepared(prompt, query_results=query_results))


async def test_graph_rechecks_all_candidates_and_does_not_promote_inferred_ids(monkeypatch):
    @asynccontextmanager
    async def transaction():
        yield AsyncMock()

    monkeypatch.setattr(runtime, "sessions", SimpleNamespace(begin=transaction))
    run = object.__new__(runtime.Run)
    run.authorize = AsyncMock()
    run.budget = AsyncMock()
    short = {"id": PRODUCT, "sku": "P1", "name": "螺丝"}
    longer = {"id": OTHER, "sku": "P2", "name": "自攻螺丝"}
    evidence = Mock()
    evidence.model_copy.return_value.model_dump.return_value = {"id": "e1"}
    query = AsyncMock(
        side_effect=[
            SimpleNamespace(payload={"items": [short]}, evidence=evidence),
            SimpleNamespace(payload={"items": [longer]}, evidence=evidence),
            SimpleNamespace(payload=short, evidence=evidence),
        ]
    )
    monkeypatch.setattr(runtime, "execute_tool", query)
    data = {
        "prompt": "自攻螺丝数量2个",
        "selected_ids": [],
        "decision": {"action": "query", "tool": "search_products", "arguments": {}},
    }
    first = await run.act(data)
    assert first["resolved_ids"] == [PRODUCT]
    second = await run.act({**data, **first})
    assert second["resolved_ids"] == [OTHER]
    assert set(second["entities"]) == {PRODUCT, OTHER}
    third = await run.act(
        {
            **data,
            **second,
            "decision": {"action": "query", "tool": "get_product", "arguments": {}},
        }
    )
    assert third["resolved_ids"] == [OTHER]
    assert run.budget.await_count == 3
