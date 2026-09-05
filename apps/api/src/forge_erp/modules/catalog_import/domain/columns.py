"""Allowlisted workbook columns mapped to existing catalog command fields."""

from dataclasses import dataclass

from forge_erp.modules.catalog.infrastructure.resources import RESOURCES


@dataclass(frozen=True)
class Column:
    field: str
    kind: str = "text"
    reference: str | None = None
    required: bool = False


def col(field, kind="text", reference=None, required=False):
    return Column(field, kind, reference, required)


COMMON = {
    "编码": col("code", "identifier", required=True),
    "名称": col("name", required=True),
    "备注": col("notes"),
}
CONTACT = {
    "联系人": col("contact"),
    "电话": col("phone"),
    "邮箱": col("email"),
    "地址": col("address"),
}
COLUMNS = {
    "categories": COMMON
    | {
        "父分类编码": col("parent_id", "identifier", "categories"),
        "属性模板": col("attribute_schema", "json"),
    },
    "brands": COMMON,
    "units": COMMON,
    "customers": COMMON | CONTACT | {"价格等级": col("price_tier")},
    "suppliers": COMMON | CONTACT,
    "warehouses": COMMON | {"地址": col("address")},
    "products": {
        "商品编码": col("sku", "identifier", required=True),
        "条码": col("barcode", "identifier"),
        "商品名称": col("name", required=True),
        "简称": col("short_name"),
        "分类编码": col("category_id", "identifier", "categories", True),
        "品牌编码": col("brand_id", "identifier", "brands"),
        "型号": col("model"),
        "规格": col("specification"),
        "基础单位编码": col("base_unit_id", "identifier", "units", True),
        "默认采购单位编码": col("default_purchase_unit_id", "identifier", "units"),
        "默认销售单位编码": col("default_sales_unit_id", "identifier", "units"),
        "最低库存数量": col("min_stock_qty", "decimal"),
        "建议补货数量": col("reorder_qty", "decimal"),
        "首选供应商编码": col("preferred_supplier_id", "identifier", "suppliers"),
        "备注": col("notes"),
    },
    "product-units": {
        "商品编码": col("product_id", "identifier", "products", True),
        "单位编码": col("unit_id", "identifier", "units", True),
        "换算率": col("unit_to_base_factor", "decimal", required=True),
        "备注": col("notes"),
    },
    "product-prices": {
        "商品编码": col("product_id", "identifier", "products", True),
        "价格类型": col("price_type", required=True),
        "客户编码": col("customer_id", "identifier", "customers"),
        "价格": col("price", "decimal", required=True),
        "备注": col("notes"),
    },
    "supplier-products": {
        "供应商编码": col("supplier_id", "identifier", "suppliers", True),
        "商品编码": col("product_id", "identifier", "products", True),
        "供应商货号": col("supplier_sku", "identifier", required=True),
        "采购单位编码": col("purchase_unit_id", "identifier", "units", True),
        "提前期天数": col("lead_days", "integer"),
        "备注": col("notes"),
    },
}
KEYS = {
    **{
        name: ("code",)
        for name in ("categories", "brands", "units", "customers", "suppliers", "warehouses")
    },
    "products": ("sku",),
    "product-units": ("product_id", "unit_id"),
    "product-prices": ("product_id", "price_type", "customer_id"),
    "supplier-products": ("supplier_id", "product_id"),
}


def require_read(ctx, resource):
    ctx.require("catalog.import.read")
    ctx.require(RESOURCES[resource].permission + ".read")


def require_write(ctx, resource):
    require_read(ctx, resource)
    ctx.require("catalog.import.write")
    ctx.require(RESOURCES[resource].permission + ".write")
