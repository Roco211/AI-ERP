"""Narrow public-chat guards; business facts still require the existing Query DTOs."""

import re

from forge_erp.core.errors import Problem

BUSINESS_NOUN = (
    r"库存|销售额|营业额|利润|营收|订单|单价|应收|应付|收款|付款|毛利|仓库|"
    r"inventory|stock|revenue|profit|orders?|receivables?|payables?"
)
BUSINESS_REQUEST = re.compile(
    rf"(?:查|查询|查看|显示|统计|列出|核对|多少|show|check|list).{{0,25}}(?:{BUSINESS_NOUN})"
    rf"|(?:{BUSINESS_NOUN}).{{0,25}}(?:多少|余额|总额|金额|数量|几件|几箱|几单|how much|how many)"
    rf"|(?:本店|店里|我们店|我们公司|本公司|our (?:store|company)).{{0,25}}(?:{BUSINESS_NOUN})"
    r"|(?:确认|创建|提交|批准|执行|取消|作废|过账).{0,12}(?:订单|单据|销售单|采购单|付款|出库|入库)",
    re.IGNORECASE,
)
SECRET_REQUEST = re.compile(
    r"(?:泄露|透露|显示|打印|输出|复述|reveal|print|show).{0,16}"
    r"(?:系统提示词|密钥|密码|system prompt|api.?key|secret|password)"
    r"|(?:忽略|无视).{0,8}(?:规则|系统指令)",
    re.IGNORECASE,
)
NUMBER = re.compile(r"\d|[零〇一二两三四五六七八九十百千万亿]+")
BUSINESS = re.compile(BUSINESS_NOUN, re.IGNORECASE)
BUSINESS_ASSERTION = re.compile(
    r"(?:库存|仓库).{0,15}(?:充足|不足|有货|缺货|售罄|可用)"
    r"|(?:已经|已|成功).{0,12}(?:创建|确认|付款|收款|出库|入库|过账|取消订单)"
    r"|(?:I|we).{0,15}(?:created|confirmed|paid|posted).{0,15}(?:order|payment|shipment)",
    re.IGNORECASE,
)


def chat_route(prompt: str) -> str | None:
    """Fail closed only for explicit enterprise requests, not everyday numbers/topics."""
    if SECRET_REQUEST.search(prompt):
        return "unsupported"
    if BUSINESS_REQUEST.search(prompt):
        return "business"
    return None


def validate_public_chat(text: str) -> None:
    """Validate whole received sentences before exposing their real content deltas.

    This is a second guard against obvious routing mistakes, not a replacement
    for the structured decision policy. Casual text has no business evidence.
    Ordinary dates, quantities, general knowledge and personal numbers remain allowed.
    """
    if (
        (BUSINESS.search(text) and NUMBER.search(text))
        or BUSINESS_ASSERTION.search(text)
        or re.search(r"<\s*/?\s*(?:think|analysis|reasoning)\b", text, re.IGNORECASE)
    ):
        raise Problem(503, "AI_CHAT_SCOPE", "闲聊回复包含未经核对的业务内容")
