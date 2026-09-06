"""Untrusted model decisions have a small, closed vocabulary."""

from typing import Annotated, Literal

from pydantic import Field, JsonValue, TypeAdapter

from forge_erp.modules.assistant.domain.drafts import DraftInput
from forge_erp.modules.catalog.domain.schemas import InputModel


class Query(InputModel):
    tool: Annotated[str, Field(min_length=1, max_length=80)]
    arguments: dict[str, JsonValue]


class QueryDecision(Query):
    action: Literal["query"]


class QueriesDecision(InputModel):
    action: Literal["queries"]
    queries: Annotated[list[Query], Field(min_length=1, max_length=8)]


class AnswerDecision(InputModel):
    action: Literal["answer"]
    code: Literal["results", "clarify", "unsupported", "no_matches", "insufficient_data"]
    evidence_ids: Annotated[list[str], Field(max_length=8)] = Field(default_factory=list)
    missing_fields: list[
        Literal[
            "product",
            "customer",
            "supplier",
            "warehouse",
            "unit",
            "qty",
            "price",
            "period",
            "scope",
        ]
    ] = Field(default_factory=list)


class DraftDecision(InputModel):
    action: Literal["draft"]
    draft: DraftInput


class ChatDecision(InputModel):
    action: Literal["chat"]


Decision = Annotated[
    QueryDecision | QueriesDecision | AnswerDecision | DraftDecision | ChatDecision,
    Field(discriminator="action"),
]
DECISION = TypeAdapter(Decision)

ANSWER_TEXT = {
    "results": "已按当前权限查询，结果和来源如下。金额、数量及统计口径以这些系统记录为准。",
    "clarify": "需要补充或明确以下条件。若有多个候选，请提供准确编码或名称，再继续查询或生成草稿。",
    "unsupported": (
        "当前助手支持资料、库存和经营查询，以及复核后创建销售或采购草稿。此操作暂未开放。"
    ),
    "no_matches": "当前查询条件没有找到匹配记录，请核对名称、编码或范围。",
    "insufficient_data": (
        "现有记录不足以确定答案，请补充条件或查看来源资料。未设置到期日，不能判断逾期。"
    ),
}
FIELD_LABELS = {
    "product": "商品",
    "customer": "客户",
    "supplier": "供应商",
    "warehouse": "仓库",
    "unit": "单位",
    "qty": "数量（数字）",
    "price": "单价（数字）",
    "period": "日期期间",
    "scope": "查询范围",
}
