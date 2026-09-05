"""AI query adapters. Business queries own facts and authorization; no SQL lives here."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.context import RuntimeContext
from forge_erp.core.errors import Problem
from forge_erp.modules.assistant.domain import tools as t
from forge_erp.modules.assistant.domain.tools import Evidence, Fact, SourceLink, ToolResult
from forge_erp.modules.catalog.application import search as catalog_search
from forge_erp.modules.catalog.application import service as catalog
from forge_erp.modules.catalog.domain import schemas as c
from forge_erp.modules.catalog.domain.values import ConversionSnapshot
from forge_erp.modules.funds.application import queries as funds
from forge_erp.modules.funds.domain import schemas as f
from forge_erp.modules.inventory.application import queries as inventory
from forge_erp.modules.inventory.domain import schemas as i
from forge_erp.modules.purchasing.application import queries as purchasing
from forge_erp.modules.purchasing.domain.schemas import PurchasePricesPage
from forge_erp.modules.replenishment.application import queries as replenishment
from forge_erp.modules.replenishment.domain.schemas import SuggestionsPage
from forge_erp.modules.reporting.application import queries as reporting
from forge_erp.modules.reporting.domain.schemas import (
    CurrentFunds,
    OperatingOverview,
    ReportSources,
)
from forge_erp.modules.sales.application import pricing
from forge_erp.modules.sales.application import queries as sales
from forge_erp.modules.sales.domain.schemas import PriceQuote, SalesPriceHistoryPage

MAX_PAYLOAD_BYTES = 24_000
MAX_ROWS = 25
OMIT = frozenset(
    {
        "notes",
        "search_text",
        "contact",
        "phone",
        "email",
        "address",
        "reason",
        "updated_at",
    }
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    input_model: type[BaseModel]
    permissions: frozenset[str]
    read_only_snapshot: bool = True
    any_permissions: frozenset[str] = frozenset()

    def allowed(self, ctx: RuntimeContext) -> bool:
        return (
            "ai.use" in ctx.permissions
            and self.permissions <= ctx.permissions
            and (not self.any_permissions or bool(self.any_permissions & ctx.permissions))
        )


def spec(name, title, model, permissions, description, *, snapshot=True, any_permissions=()):
    return ToolSpec(
        name,
        title,
        description,
        model,
        frozenset(permissions),
        snapshot,
        frozenset(any_permissions),
    )


_SPECS = [
    spec(
        "search_products",
        "商品搜索",
        t.ProductSearchInput,
        {"catalog.read"},
        "按编码、条码、规格、属性和关键词搜索在用商品；多个候选必须让用户消歧。",
    ),
    spec(
        "get_product",
        "商品资料",
        t.ProductInput,
        {"catalog.read"},
        "读取已解析商品 ID 的资料；价格仅在当前用户有对应权限时返回。",
    ),
    spec(
        "get_product_units",
        "商品单位",
        t.ProductUnitsInput,
        {"catalog.read"},
        "列出商品有效单位名称、ID 及到基本单位的换算因子。",
    ),
    spec(
        "search_units",
        "单位查找",
        t.SearchInput,
        {"catalog.read"},
        "查找在用单位的名称、编码与 ID；不要猜测单位 ID。",
    ),
    spec(
        "convert_product_unit",
        "数量换算",
        t.ConversionInput,
        {"catalog.read"},
        "服务器按已解析商品、单位和十进制字符串数量换算；不接受浮点数。",
    ),
    spec(
        "search_customers",
        "客户查找",
        t.SearchInput,
        {"customer.read"},
        "查找在用客户编码、名称和 ID，不提供联系方式或备注。",
    ),
    spec(
        "search_suppliers",
        "供应商查找",
        t.SearchInput,
        {"supplier.read"},
        "查找在用供应商编码、名称和 ID，不提供联系方式或备注。",
    ),
    spec(
        "search_warehouses",
        "仓库查找",
        t.SearchInput,
        {"warehouse.read"},
        "查找在用仓库编码、名称和 ID。",
    ),
    spec(
        "get_inventory",
        "库存余额",
        t.InventoryInput,
        {"inventory.read"},
        "按仓库和商品读取现存、占用和可用库存；这是当前余额，不是历史期末。",
    ),
    spec(
        "get_inventory_movements",
        "库存流水",
        t.MovementsInput,
        {"inventory.read"},
        "读取不可变库存事实；分页必须原样传回签名游标，不得解释或构造游标。",
    ),
    spec(
        "get_low_stock_products",
        "最低库存预警",
        t.PageInput,
        {"inventory.read"},
        "组织内全部仓库的可用库存低于或等于最低库存的商品。",
    ),
    spec(
        "get_operating_overview",
        "经营概览",
        t.ReportInput,
        {"dashboard.read"},
        "期间销售、实际成本毛利和现金；应收应付及库存是查询时的当前余额。最多366日。",
    ),
    spec(
        "get_report_sources",
        "经营指标来源",
        t.ReportSourcesInput,
        {"dashboard.read"},
        "读取指定经营指标的原始单据来源；当前余额不随期间筛选变为历史余额。",
    ),
    spec(
        "get_funds_status",
        "资金启用状态",
        t.EmptyInput,
        set(),
        "检查资金是否启用及旧单衔接状态。未启用或未完整衔接不代表无欠款。",
        any_permissions={"funds.ar.read", "funds.ap.read"},
    ),
    spec(
        "get_funds_sources",
        "未结清往来",
        t.FundsSourcesInput,
        set(),
        "读取未结清应收应付或待退款来源。系统没有账期和到期日，不能判断逾期。",
        any_permissions={"funds.ar.read", "funds.ap.read"},
    ),
    spec(
        "get_replenishment_suggestions",
        "补货建议",
        t.ReplenishmentInput,
        set(replenishment.READ_PERMISSIONS),
        "读取确定性补货算法的依据和数量；30个完整业务日，7日复核期，不让模型计算数量。",
    ),
    spec(
        "get_sales_quote",
        "客户销售报价",
        t.SalesQuoteInput,
        {"sales.read", "product.price.read", "customer.read", "catalog.read"},
        "当前服务器销售报价及来源；最新历史价不代表用户指定月份的历史价。",
        snapshot=False,
    ),
    spec(
        "get_customer_price_history",
        "客户成交历史",
        t.SalesHistoryInput,
        {"sales.read", "product.price.read"},
        "已出库销售单的成交历史和冻结单位换算；按实际过账时间倒序，含退货。",
        snapshot=False,
    ),
    spec(
        "get_supplier_purchase_history",
        "供应商采购历史",
        t.PurchaseHistoryInput,
        {"purchase.read", "product.cost.read"},
        "已入库采购单的历史单价、单位和来源。不同单位的历史价不能直接套用。",
    ),
]
TOOL_REGISTRY: dict[str, ToolSpec] = {item.name: item for item in _SPECS}


def get_tool_schemas(ctx: RuntimeContext) -> list[dict]:
    """Return only visible tools; execution repeats authorization independently."""
    return [
        {
            "name": item.name,
            "description": item.description,
            "parameters": item.input_model.model_json_schema(),
            "read_only_snapshot": item.read_only_snapshot,
        }
        for item in TOOL_REGISTRY.values()
        if item.allowed(ctx)
    ]


def model_values(data: Any) -> Any:
    # Application queries can compose independently typed DTOs (e.g. SuggestionCounts
    # inside OperatingOverview). Normalize those models before the public DTO validates.
    if isinstance(data, BaseModel):
        return model_values(data.model_dump(mode="python"))
    if isinstance(data, dict):
        return {key: model_values(value) for key, value in data.items()}
    if isinstance(data, list):
        return [model_values(value) for value in data]
    return data


def public(schema: type[BaseModel], data: Any) -> dict:
    """Reapply the HTTP response DTO before a raw query result crosses the AI boundary."""
    return schema.model_validate(model_values(data)).model_dump(mode="json", exclude_none=True)


async def query(db: AsyncSession, ctx: RuntimeContext, name: str, args: Any) -> dict:
    if name == "search_products":
        attributes = {args.attribute_key: args.attribute_value} if args.attribute_key else None
        data = await catalog_search.search_products(
            db,
            ctx,
            args.q,
            args.page,
            args.page_size,
            True,
            {"category_id": args.category_id, "brand_id": args.brand_id},
            attributes,
        )
        result = public(c.Page[c.ProductRead], data)
        for row in result["items"]:
            # Detail lookup supplies attributes when needed, not 25 full schemas per search.
            row.pop("attributes", None)
        return result
    if name == "get_product":
        return public(c.ProductRead, await catalog.get_record(db, ctx, "products", args.product_id))
    if name == "get_product_units":
        result = public(
            c.Page[c.ProductUnitRead],
            await catalog.list_records(
                db,
                ctx,
                "product-units",
                "",
                args.page,
                args.page_size,
                True,
                {"product_id": args.product_id},
            ),
        )
        for row in result["items"]:
            unit = public(
                c.UnitRead, await catalog.get_record(db, ctx, "units", UUID(row["unit_id"]))
            )
            row["unit_name"] = unit["name"]
            row["unit_code"] = unit["code"]
        return result
    resources = {
        "search_customers": ("customers", c.CustomerRead),
        "search_suppliers": ("suppliers", c.SupplierRead),
        "search_warehouses": ("warehouses", c.WarehouseRead),
        "search_units": ("units", c.UnitRead),
    }
    if name in resources:
        resource, schema = resources[name]
        return public(
            c.Page[schema],
            await catalog.list_records(
                db,
                ctx,
                resource,
                args.q,
                args.page,
                args.page_size,
                True,
            ),
        )
    if name == "convert_product_unit":
        return public(
            ConversionSnapshot,
            await catalog_search.conversion(
                db,
                ctx,
                args.product_id,
                args.unit_id,
                Decimal(args.qty),
            ),
        )
    if name == "get_inventory":
        return public(
            i.InventoryBalancesPage,
            await inventory.balances(
                db,
                ctx,
                args.warehouse_id,
                args.product_id,
                args.q,
                args.page,
                args.page_size,
            ),
        )
    if name == "get_inventory_movements":
        return public(
            i.InventoryMovementsPage,
            await inventory.movements(
                db,
                ctx,
                args.warehouse_id,
                args.product_id,
                args.document_id,
                args.page_size,
                args.cursor,
            ),
        )
    if name == "get_low_stock_products":
        return public(i.LowStockPage, await inventory.low_stock(db, ctx, args.page, args.page_size))
    if name == "get_operating_overview":
        data = public(
            OperatingOverview,
            await reporting.overview(
                db,
                ctx,
                args.date_from,
                args.date_to,
            ),
        )
        for value in data.values():
            if isinstance(value, dict):
                value.pop("daily", None)
        return data
    if name == "get_report_sources":
        return public(
            ReportSources,
            await reporting.sources(
                db,
                ctx,
                args.metric,
                args.date_from,
                args.date_to,
                args.page,
                args.page_size,
            ),
        )
    if name == "get_funds_status":
        data = public(f.FundsSettings, await funds.settings(db, ctx))
        for side in ("AR", "AP"):
            if f"funds.{side.lower()}.read" in ctx.permissions:
                # Same Application integration policy as the ordinary operating overview.
                data[side] = public(CurrentFunds, await reporting.integration_state(db, ctx, side))
        return data
    if name == "get_funds_sources":
        ctx.require(f"funds.{args.side.lower()}.read")
        data = public(
            f.FundsSourcesPage,
            await funds.sources(
                db,
                ctx,
                args.side,
                args.page,
                args.page_size,
                args.party_id,
                args.status,
                args.q,
            ),
        )
        data.update(public(CurrentFunds, await reporting.integration_state(db, ctx, args.side)))
        data["side"] = args.side
        data["due_dates_available"] = False
        return data
    if name == "get_replenishment_suggestions":
        return public(
            SuggestionsPage,
            await replenishment.suggestions(
                db,
                ctx,
                args.page,
                args.page_size,
                args.category_id,
                args.supplier_id,
                args.q,
                args.candidate_only,
                args.suggested_only,
            ),
        )
    if name == "get_sales_quote":
        return public(
            PriceQuote,
            await pricing.quote(
                db,
                ctx,
                args.customer_id,
                args.product_id,
                args.unit_id,
            ),
        )
    if name == "get_customer_price_history":
        return public(
            SalesPriceHistoryPage,
            await sales.price_history(
                db,
                ctx,
                args.page,
                args.page_size,
                args.customer_id,
                args.product_id,
            ),
        )
    if name == "get_supplier_purchase_history":
        return public(
            PurchasePricesPage,
            await purchasing.price_history(
                db,
                ctx,
                args.page,
                args.page_size,
                args.supplier_id,
                args.product_id,
            ),
        )
    raise Problem(422, "AI_TOOL_NOT_ALLOWED", "此工具未开放")


LABELS = {
    "sku": "商品编码",
    "name": "名称",
    "code": "编码",
    "product_name": "商品",
    "product_label": "商品",
    "specification": "规格",
    "unit_name": "单位",
    "unit_code": "单位编码",
    "unit_label": "单位",
    "base_unit_name": "基本单位",
    "warehouse_name": "仓库",
    "party_name": "往来单位",
    "customer_name": "客户",
    "supplier_name": "供应商",
    "preferred_supplier_name": "首选供应商",
    "number": "编号",
    "document_number": "来源单号",
    "qty": "数量",
    "base_qty": "基本数量",
    "unit_to_base_factor": "换算因子",
    "conversion_version": "换算版本",
    "on_hand_qty": "现存数量",
    "reserved_qty": "占用数量",
    "available_qty": "可用数量",
    "inventory_value": "库存价值",
    "avg_unit_cost": "平均成本",
    "reserved_qty_delta": "占用变动",
    "value_delta": "价值变动",
    "kind": "类型",
    "unit_price": "单价",
    "amount": "金额",
    "standard_price": "标准价",
    "retail_price": "零售价",
    "wholesale_price": "批发价",
    "status": "状态",
    "posted_at": "过账时间",
    "created_at": "记录时间",
    "min_stock_qty": "最低库存",
    "balance": "当前净余额",
    "settlement_amount": "待结算",
    "refund_amount": "待退款",
    "commercial_amount": "商业金额",
    "settled_amount": "已结算",
    "refunded_amount": "已退款",
    "net_sales_amount": "销售净额",
    "shipment_amount": "出库销售额",
    "return_amount": "退货销售额",
    "shipment_cost": "出库成本",
    "return_cost": "退回成本",
    "net_cost": "净成本",
    "gross_margin": "销售毛利",
    "cost_status": "成本事实",
    "shipment_count": "出库单数",
    "return_count": "退货单数",
    "net_cash_amount": "净收付额",
    "source_count": "来源数",
    "product_count": "库存商品数",
    "low_stock_count": "最低库存预警数",
    "valuation": "当前库存估值",
    "candidate_count": "补货候选数",
    "suggested_count": "正建议数量商品数",
    "integration_status": "资金衔接",
    "unmapped_document_count": "未衔接来源数",
    "enabled": "资金启用",
    "business_date": "业务日期",
    "business_today": "业务当日",
    "business_timezone": "业务时区",
    "open_purchase_qty": "采购在途",
    "suggested_base_qty": "建议采购基本数量",
    "days_of_stock": "可支撑天数",
    "daily_sales_qty": "日均销量",
    "net_sales_qty": "净销售基本数量",
    "reorder_point": "补货触发点",
    "target_stock": "目标库存",
    "lead_days": "交期天数",
    "safety_stock_qty": "安全库存",
    "minimum_reorder_qty": "最低补货量",
    "inventory_position": "可用加在途",
    "gap": "补货缺口",
    "label": "来源",
    "signed_amount": "净额",
    "signed_cost": "净成本",
    "actual_cost": "实际成本",
    "date": "业务日期",
    "source": "报价来源",
    "returned_qty": "已退数量",
    "returned_base_qty": "已退基本数量",
    "returned_amount": "已退金额",
    "price_tier": "客户价格级别",
    "active": "在用",
    "version": "资料版本",
    "algorithm_version": "补货算法",
    "reasons": "建议依据",
    "window_start": "销量窗口起点",
    "window_end": "销量窗口终点",
    "candidate": "补货候选",
}
VALUES = {
    "NOT_ENABLED": "未启用",
    "INCOMPLETE": "旧来源尚未完整衔接",
    "ACTIVE": "已启用且完整衔接",
    "MISSING_FACTS": "成本事实缺失，不能确定毛利",
    "AVAILABLE": "可用",
    "OPEN": "未结清",
    "REFUND": "待退款",
    "SETTLED": "已结清",
    "REVERSED": "已冲销",
    "POSTED": "已过账",
    "SHIPMENT": "销售出库",
    "RECEIPT": "采购入库",
    "RETURN": "退货",
    "RECEIVE": "入库",
    "ISSUE": "出库",
    "RESERVE": "占用",
    "RELEASE": "释放占用",
    "OPENING": "期初",
    "ADJUSTMENT": "调整",
    "LEGACY": "旧来源衔接",
    "SETTLEMENT": "收付款结算",
    "history": "最新有效成交历史",
    "customer": "客户专价",
    "standard": "标准价",
    "retail": "零售价",
    "wholesale": "批发价",
    "manual": "手工输入",
    "unset": "未设置",
    "NO_SALES_HISTORY": "窗口内无销售历史",
    "NON_POSITIVE_NET_SALES": "窗口净销量不为正",
    "MISSING_LEAD_DAYS": "缺供货交期，按静态库存规则建议",
    "ABOVE_REORDER_POINT": "可用库存高于补货触发点",
    "INBOUND_COVERS_TARGET": "采购在途已覆盖目标",
    "TARGET_COVERED": "当前库存已覆盖目标",
    "NO_DEMAND_BASIS": "缺少需求依据",
    "REPLENISHMENT_SUGGESTED": "达到补货触发条件且存在缺口",
}
MONEY_FIELDS = frozenset(
    {
        "inventory_value",
        "valuation",
        "value_delta",
        "balance",
        "amount",
        "commercial_amount",
        "settlement_amount",
        "refund_amount",
        "settled_amount",
        "refunded_amount",
        "net_sales_amount",
        "shipment_amount",
        "return_amount",
        "shipment_cost",
        "return_cost",
        "net_cost",
        "gross_margin",
        "net_cash_amount",
        "signed_amount",
        "signed_cost",
        "actual_cost",
        "returned_amount",
    }
)
TABLE_FIELDS = {
    "search_products": (
        "sku",
        "name",
        "specification",
        "standard_price",
        "retail_price",
        "wholesale_price",
    ),
    "get_product": (
        "sku",
        "name",
        "specification",
        "standard_price",
        "retail_price",
        "wholesale_price",
    ),
    "get_product_units": ("unit_code", "unit_name", "unit_to_base_factor", "version"),
    "get_inventory": (
        "sku",
        "product_name",
        "warehouse_name",
        "unit_name",
        "on_hand_qty",
        "reserved_qty",
        "available_qty",
        "inventory_value",
        "avg_unit_cost",
    ),
    "get_inventory_movements": (
        "created_at",
        "document_number",
        "product_label",
        "warehouse_name",
        "kind",
        "base_qty",
        "reserved_qty_delta",
        "value_delta",
    ),
    "get_low_stock_products": (
        "sku",
        "product_name",
        "unit_name",
        "available_qty",
        "min_stock_qty",
    ),
    "get_funds_sources": (
        "number",
        "party_name",
        "status",
        "business_date",
        "balance",
        "settlement_amount",
        "refund_amount",
    ),
    "get_replenishment_suggestions": (
        "sku",
        "name",
        "base_unit_name",
        "available_qty",
        "open_purchase_qty",
        "reorder_point",
        "target_stock",
        "suggested_base_qty",
        "preferred_supplier_name",
        "lead_days",
        "reasons",
    ),
    "get_customer_price_history": (
        "document_number",
        "customer_name",
        "product_label",
        "unit_label",
        "qty",
        "unit_price",
        "amount",
        "returned_qty",
        "returned_amount",
        "posted_at",
    ),
    "get_supplier_purchase_history": (
        "document_number",
        "supplier_name",
        "product_label",
        "unit_label",
        "qty",
        "unit_price",
        "amount",
        "posted_at",
    ),
    "get_report_sources": (
        "number",
        "label",
        "party_name",
        "kind",
        "date",
        "unit_name",
        "qty",
        "signed_amount",
        "signed_cost",
        "gross_margin",
        "cost_status",
        "balance",
        "settlement_amount",
        "refund_amount",
        "available_qty",
        "reserved_qty",
        "valuation",
        "suggested_base_qty",
    ),
}
SOURCE_PATHS = {
    "search_products": "/products",
    "get_product": "/products",
    "get_product_units": "/products",
    "search_units": "/settings/units",
    "search_customers": "/customers",
    "search_suppliers": "/suppliers",
    "search_warehouses": "/settings/warehouses",
    "convert_product_unit": "/products",
    "get_inventory": "/inventory",
    "get_inventory_movements": "/inventory?tab=movements",
    "get_low_stock_products": "/inventory",
    "get_operating_overview": "/dashboard",
    "get_report_sources": "/dashboard",
    "get_funds_status": "/funds",
    "get_funds_sources": "/funds",
    "get_replenishment_suggestions": "/replenishment",
    "get_sales_quote": "/sales",
    "get_customer_price_history": "/sales",
    "get_supplier_purchase_history": "/purchase",
}


def display(value: Any) -> str:
    if value is None:
        return "未提供"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, list):
        return "；".join(display(item) for item in value)
    return VALUES.get(str(value), str(value))


def minimize(value: Any, field: str = "") -> tuple[Any, bool]:
    """Bound business text, preserve decimal strings exactly, and omit contact/note content."""
    if isinstance(value, dict):
        result, changed = {}, False
        for key, item in value.items():
            if key in OMIT:
                continue
            result[key], shortened = minimize(item, key)
            changed |= shortened
        return result, changed
    if isinstance(value, list):
        result, changed = [], len(value) > MAX_ROWS
        for item in value[:MAX_ROWS]:
            safe, shortened = minimize(item, field)
            result.append(safe)
            changed |= shortened
        return result, changed
    if (
        isinstance(value, str)
        and len(value) > 600
        and field not in {"cursor", "next_cursor", "cutoff"}
    ):
        return value[:599] + "…", True
    return value, False


def source_links(name: str, payload: dict, rows: list[dict]) -> list[SourceLink]:
    links = [SourceLink(label="打开" + TOOL_REGISTRY[name].title, href=SOURCE_PATHS[name])]
    for row in rows:
        path = None
        document_id = row.get("document_id")
        if name == "get_customer_price_history" and document_id:
            path = "/sales?document=" + str(UUID(document_id))
        elif name == "get_supplier_purchase_history" and document_id:
            path = "/purchase?document=" + str(UUID(document_id))
        elif name == "get_funds_sources" and row.get("id"):
            path = f"/funds?side={payload['side']}&source={UUID(row['id'])}"
        elif name == "get_inventory_movements" and document_id:
            path = "/inventory?tab=movements&document=" + str(UUID(document_id))
        elif name == "get_report_sources":
            if payload.get("metric") == "sales" and document_id:
                path = "/sales?document=" + str(UUID(document_id))
            elif payload.get("metric") in {"current_ar", "current_ap"} and row.get("id"):
                side = "AR" if payload["metric"] == "current_ar" else "AP"
                path = f"/funds?side={side}&source={UUID(row['id'])}"
        if path and all(link.href != path for link in links):
            label = row.get("document_number") or row.get("number") or "查看来源"
            links.append(SourceLink(label=label, href=path))
    return links[: MAX_ROWS + 1]


def evidence(name: str, payload: dict, truncated: bool, as_of: datetime) -> Evidence:
    rows = payload.get("items", [])
    if name == "get_product":
        rows = [payload]
    fields = TABLE_FIELDS.get(name, ("code", "name"))
    columns = [key for key in fields if any(key in row for row in rows)]
    summary = []
    if "total" in payload:
        summary.extend(
            [
                Fact(label="符合条件记录", value=str(payload["total"]), unit="条"),
                Fact(label="本页展示", value=str(len(rows)), unit="条"),
            ]
        )
    if payload.get("next_cursor"):
        truncated = True
    if "total" in payload:
        truncated |= payload["total"] > len(rows)
    if not rows and "items" in payload:
        summary.append(Fact(label="查询结果", value="当前筛选没有记录"))

    def add_facts(data: dict, prefix="", labels=None):
        for key, value in data.items():
            if key in LABELS and not isinstance(value, (list, dict)):
                summary.append(
                    Fact(
                        label=prefix + (labels or {}).get(key, LABELS[key]),
                        value=display(value),
                        unit="元" if key in MONEY_FIELDS else None,
                    )
                )

    if name == "get_operating_overview":
        for key, title in (
            ("sales", "期间销售 · "),
            ("cash_ar", "期间收款 · "),
            ("cash_ap", "期间付款 · "),
            ("current_ar", "当前应收 · "),
            ("current_ap", "当前应付 · "),
            ("inventory", "当前库存 · "),
            ("replenishment", "补货 · "),
        ):
            if key in payload:
                cash_labels = (
                    {
                        "settlement_amount": "实收" if key == "cash_ar" else "实付",
                        "refund_amount": "退客户款" if key == "cash_ar" else "供应商退款",
                        "net_cash_amount": "净收款" if key == "cash_ar" else "净付款",
                    }
                    if key.startswith("cash_")
                    else None
                )
                add_facts(payload[key], title, cash_labels)
    elif name == "get_funds_status":
        add_facts(payload)
        for side, title in (("AR", "应收 · "), ("AP", "应付 · ")):
            if side in payload:
                add_facts(payload[side], title)
    elif name in {"convert_product_unit", "get_sales_quote"}:
        add_facts(payload)
        if name == "get_sales_quote":
            if "unit_price" not in payload:
                summary.append(Fact(label="单价", value="未设置，请明确价格"))
            add_facts(payload.get("price_source", {}))
    else:
        for key in ("integration_status", "unmapped_document_count", "algorithm_version"):
            if key in payload:
                add_facts({key: payload[key]})
    scope = "本次查询的组织数据；各次工具查询时点独立。"
    if "date_from" in payload:
        scope = (
            f"期间：{payload['date_from']} 至 {payload['date_to']}；"
            f"业务时区：{payload['business_timezone']}。"
            "应收应付和库存为当前余额，不是历史期末。"
        )
        summary.append(Fact(label="统计口径", value=payload["restatement_notice"]))
    if "window_start" in payload:
        scope = (
            f"销量窗口：{payload['window_start']} 至 {payload['window_end']}；"
            f"业务时区：{payload['business_timezone']}。当前库存与在途，7日复核期。"
        )
    if name in {"get_funds_sources", "get_funds_status"}:
        summary.append(Fact(label="账期口径", value="未设置到期日，不能判断逾期"))
    if name == "get_inventory_movements":
        summary.append(Fact(label="数量口径", value="基本数量以商品基本单位计，原单据单位可能不同"))
    if payload.get("as_of"):
        as_of = datetime.fromisoformat(payload["as_of"])
    return Evidence(
        id="",
        tool=name,
        title=TOOL_REGISTRY[name].title,
        as_of=as_of,
        scope=scope,
        summary=summary,
        columns=[LABELS[key] for key in columns],
        rows=[
            {LABELS[key]: display(row[key]) if key in row else None for key in columns}
            for row in rows
        ],
        links=source_links(name, payload, rows),
        truncated=truncated,
    )


async def execute_tool(
    db: AsyncSession,
    ctx: RuntimeContext,
    name: str,
    arguments: dict,
) -> ToolResult:
    ctx.require("ai.use")
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        raise Problem(422, "AI_TOOL_NOT_ALLOWED", "此工具未开放")
    if not tool.allowed(ctx):
        raise Problem(403, "PERMISSION_DENIED", "当前账户无权使用此查询")
    try:
        args = tool.input_model.model_validate(arguments)
    except ValidationError as exc:
        # Do not echo arbitrary injected arguments or internal schema diagnostics.
        raise Problem(422, "AI_TOOL_ARGUMENTS_INVALID", "查询条件无效，请补充或修改条件") from exc
    started = datetime.now(UTC)
    try:
        data = await query(db, ctx, name, args)
    except ValidationError as exc:
        raise Problem(503, "AI_TOOL_RESULT_INVALID", "查询结果暂时不可用") from exc
    payload, shortened = minimize(data)
    # Never cut JSON or remove a row while retaining a cursor that would skip that row.
    if len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_PAYLOAD_BYTES:
        raise Problem(422, "AI_TOOL_RESULT_TOO_LARGE", "查询结果过大，请缩小范围或每页条数")
    return ToolResult(payload=payload, evidence=evidence(name, payload, shortened, started))
