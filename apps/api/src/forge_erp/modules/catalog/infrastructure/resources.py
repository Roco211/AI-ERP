from dataclasses import dataclass, field

from pydantic import BaseModel

from forge_erp.modules.catalog.domain import schemas as s


@dataclass(frozen=True)
class Resource:
    table: str
    schema: type[BaseModel]
    permission: str = "catalog"
    search: tuple[str, ...] = ("code", "name")
    json_fields: tuple[str, ...] = ()
    references: dict[str, str] = field(default_factory=dict)


RESOURCES = {
    "categories": Resource(
        "categories",
        s.CategoryCreate,
        json_fields=("attribute_schema",),
        references={"parent_id": "categories"},
    ),
    "brands": Resource("brands", s.BrandCreate),
    "units": Resource("units", s.UnitCreate),
    "customers": Resource("customers", s.CustomerCreate, permission="customer"),
    "suppliers": Resource("suppliers", s.SupplierCreate, permission="supplier"),
    "warehouses": Resource("warehouses", s.WarehouseCreate, permission="warehouse"),
    "products": Resource(
        "products",
        s.ProductCreate,
        search=("sku", "barcode", "name", "search_text"),
        json_fields=("attributes",),
        references={
            "category_id": "categories",
            "brand_id": "brands",
            "base_unit_id": "units",
            "default_purchase_unit_id": "units",
            "default_sales_unit_id": "units",
            "preferred_supplier_id": "suppliers",
        },
    ),
    "product-units": Resource(
        "product_units",
        s.ProductUnitCreate,
        search=("notes",),
        references={"product_id": "products", "unit_id": "units"},
    ),
    "product-prices": Resource(
        "product_prices",
        s.ProductPriceCreate,
        permission="product.price",
        search=("price_type", "notes"),
        references={"product_id": "products", "customer_id": "customers"},
    ),
    "supplier-products": Resource(
        "supplier_products",
        s.SupplierProductCreate,
        permission="supplier",
        search=("supplier_sku", "notes"),
        references={
            "supplier_id": "suppliers",
            "product_id": "products",
            "purchase_unit_id": "units",
        },
    ),
}
