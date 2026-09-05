from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def reject_float(value: object) -> object:
    if isinstance(value, float):
        raise ValueError("Use a decimal string, not a floating point number")
    return value


Amount = Annotated[
    Decimal, BeforeValidator(reject_float), Field(ge=0, max_digits=20, decimal_places=6)
]
Factor = Annotated[
    Decimal, BeforeValidator(reject_float), Field(gt=0, max_digits=20, decimal_places=6)
]
Code = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")]
Name = Annotated[str, Field(min_length=1, max_length=200)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class AttributeDefinition(InputModel):
    key: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=60)
    kind: Literal["text", "decimal", "enum", "boolean"] = "text"
    required: bool = False
    unit: str = Field(default="", max_length=20)
    options: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def enum_options(self) -> AttributeDefinition:
        if self.kind == "enum" and not self.options:
            raise ValueError("Enum attributes require options")
        if any(not x.strip() or len(x) > 100 for x in self.options):
            raise ValueError("Invalid attribute option")
        return self


class DictionaryCreate(InputModel):
    code: Code
    name: Name
    notes: str = Field(default="", max_length=2000)


class CategoryCreate(DictionaryCreate):
    parent_id: UUID | None = None
    attribute_schema: list[AttributeDefinition] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def unique_attributes(self) -> CategoryCreate:
        keys = [a.key for a in self.attribute_schema]
        if len(keys) != len(set(keys)):
            raise ValueError("Attribute keys must be unique")
        return self


class BrandCreate(DictionaryCreate):
    pass


class UnitCreate(DictionaryCreate):
    pass


class VersionInput(InputModel):
    expected_version: int = Field(ge=1)


class RecordMeta(BaseModel):
    id: UUID
    active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class CategoryRead(CategoryCreate, RecordMeta):
    pass


class BrandRead(BrandCreate, RecordMeta):
    pass


class UnitRead(UnitCreate, RecordMeta):
    pass


class CategoryUpdate(CategoryCreate, VersionInput):
    pass


class BrandUpdate(BrandCreate, VersionInput):
    pass


class UnitUpdate(UnitCreate, VersionInput):
    pass


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int


class ContactCreate(DictionaryCreate):
    contact: str = Field(default="", max_length=100)
    phone: str = Field(default="", max_length=60)
    email: str = Field(default="", max_length=254)
    address: str = Field(default="", max_length=500)


class CustomerCreate(ContactCreate):
    price_tier: Literal["standard", "retail", "wholesale"] = "standard"


class SupplierCreate(ContactCreate):
    pass


class WarehouseCreate(DictionaryCreate):
    address: str = Field(default="", max_length=500)


class ProductCreate(InputModel):
    sku: Code
    barcode: str | None = Field(default=None, max_length=100)
    name: Name
    short_name: str = Field(default="", max_length=100)
    category_id: UUID
    brand_id: UUID | None = None
    model: str = Field(default="", max_length=200)
    specification: str = Field(default="", max_length=300)
    attributes: dict[str, str | bool] = Field(default_factory=dict, max_length=32)
    base_unit_id: UUID
    default_purchase_unit_id: UUID | None = None
    default_sales_unit_id: UUID | None = None
    min_stock_qty: Amount = Decimal(0)
    reorder_qty: Amount = Decimal(0)
    preferred_supplier_id: UUID | None = None
    notes: str = Field(default="", max_length=2000)


class ProductUnitCreate(InputModel):
    product_id: UUID
    unit_id: UUID
    unit_to_base_factor: Factor
    notes: str = Field(default="", max_length=2000)


class ProductPriceCreate(InputModel):
    product_id: UUID
    price_type: Literal["standard", "retail", "wholesale", "customer"]
    customer_id: UUID | None = None
    price: Amount
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def customer_price(self) -> ProductPriceCreate:
        if (self.price_type == "customer") != (self.customer_id is not None):
            raise ValueError("Customer-specific prices require a customer, tier prices do not")
        return self


class SupplierProductCreate(InputModel):
    supplier_id: UUID
    product_id: UUID
    supplier_sku: Code
    purchase_unit_id: UUID
    lead_days: int = Field(default=0, ge=0, le=3650)
    notes: str = Field(default="", max_length=2000)


class CustomerRead(CustomerCreate, RecordMeta):
    pass


class CustomerUpdate(CustomerCreate, VersionInput):
    pass


class SupplierRead(SupplierCreate, RecordMeta):
    pass


class SupplierUpdate(SupplierCreate, VersionInput):
    pass


class WarehouseRead(WarehouseCreate, RecordMeta):
    pass


class WarehouseUpdate(WarehouseCreate, VersionInput):
    pass


class ProductRead(ProductCreate, RecordMeta):
    search_text: str
    standard_price: Amount | None = None
    retail_price: Amount | None = None
    wholesale_price: Amount | None = None


class ProductUpdate(ProductCreate, VersionInput):
    pass


class ProductUnitRead(ProductUnitCreate, RecordMeta):
    pass


class ProductUnitUpdate(ProductUnitCreate, VersionInput):
    pass


class ProductPriceRead(ProductPriceCreate, RecordMeta):
    pass


class ProductPriceUpdate(ProductPriceCreate, VersionInput):
    pass


class SupplierProductRead(SupplierProductCreate, RecordMeta):
    pass


class SupplierProductUpdate(SupplierProductCreate, VersionInput):
    pass
