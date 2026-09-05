from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge_erp.core.auth_dependencies import authenticated_transaction
from forge_erp.core.context import RuntimeContext
from forge_erp.modules.catalog.application.service import get_record, list_records, write_command
from forge_erp.modules.catalog.domain import schemas as s

router = APIRouter(prefix="/api/v1", tags=["catalog"])
Transaction = Annotated[
    tuple[AsyncSession, RuntimeContext], Depends(authenticated_transaction, scope="function")
]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]


@router.get("/categories", response_model=s.Page[s.CategoryRead], operation_id="listCategory")
async def list_categories(
    tx: Transaction,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    active: bool | None = None,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    brand_id: UUID | None = None,
    supplier_id: UUID | None = None,
):
    return await list_records(
        *tx,
        "categories",
        q,
        page,
        page_size,
        active,
        {
            "product_id": product_id,
            "category_id": category_id,
            "brand_id": brand_id,
            "supplier_id": supplier_id,
        },
    )


@router.get("/categories/{record_id}", response_model=s.CategoryRead, operation_id="getCategory")
async def get_categories(record_id: UUID, tx: Transaction):
    return await get_record(*tx, "categories", record_id)


@router.post(
    "/categories", response_model=s.CategoryRead, status_code=201, operation_id="createCategory"
)
async def create_categories(body: s.CategoryCreate, tx: Transaction, key: Key):
    return await write_command(*tx, "categories", body.model_dump(), key)


@router.put("/categories/{record_id}", response_model=s.CategoryRead, operation_id="updateCategory")
async def update_categories(record_id: UUID, body: s.CategoryUpdate, tx: Transaction, key: Key):
    return await write_command(
        *tx,
        "categories",
        body.model_dump(exclude={"expected_version"}),
        key,
        record_id,
        body.expected_version,
    )


@router.post(
    "/categories/{record_id}/activate",
    response_model=s.CategoryRead,
    operation_id="activateCategory",
)
async def activate_categories(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(
        *tx, "categories", {}, key, record_id, body.expected_version, active=True
    )


@router.post(
    "/categories/{record_id}/deactivate",
    response_model=s.CategoryRead,
    operation_id="deactivateCategory",
)
async def deactivate_categories(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(
        *tx, "categories", {}, key, record_id, body.expected_version, active=False
    )


@router.get("/brands", response_model=s.Page[s.BrandRead], operation_id="listBrand")
async def list_brands(
    tx: Transaction,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    active: bool | None = None,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    brand_id: UUID | None = None,
    supplier_id: UUID | None = None,
):
    return await list_records(
        *tx,
        "brands",
        q,
        page,
        page_size,
        active,
        {
            "product_id": product_id,
            "category_id": category_id,
            "brand_id": brand_id,
            "supplier_id": supplier_id,
        },
    )


@router.get("/brands/{record_id}", response_model=s.BrandRead, operation_id="getBrand")
async def get_brands(record_id: UUID, tx: Transaction):
    return await get_record(*tx, "brands", record_id)


@router.post("/brands", response_model=s.BrandRead, status_code=201, operation_id="createBrand")
async def create_brands(body: s.BrandCreate, tx: Transaction, key: Key):
    return await write_command(*tx, "brands", body.model_dump(), key)


@router.put("/brands/{record_id}", response_model=s.BrandRead, operation_id="updateBrand")
async def update_brands(record_id: UUID, body: s.BrandUpdate, tx: Transaction, key: Key):
    return await write_command(
        *tx,
        "brands",
        body.model_dump(exclude={"expected_version"}),
        key,
        record_id,
        body.expected_version,
    )


@router.post(
    "/brands/{record_id}/activate", response_model=s.BrandRead, operation_id="activateBrand"
)
async def activate_brands(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(
        *tx, "brands", {}, key, record_id, body.expected_version, active=True
    )


@router.post(
    "/brands/{record_id}/deactivate", response_model=s.BrandRead, operation_id="deactivateBrand"
)
async def deactivate_brands(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(
        *tx, "brands", {}, key, record_id, body.expected_version, active=False
    )


@router.get("/units", response_model=s.Page[s.UnitRead], operation_id="listUnit")
async def list_units(
    tx: Transaction,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    active: bool | None = None,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    brand_id: UUID | None = None,
    supplier_id: UUID | None = None,
):
    return await list_records(
        *tx,
        "units",
        q,
        page,
        page_size,
        active,
        {
            "product_id": product_id,
            "category_id": category_id,
            "brand_id": brand_id,
            "supplier_id": supplier_id,
        },
    )


@router.get("/units/{record_id}", response_model=s.UnitRead, operation_id="getUnit")
async def get_units(record_id: UUID, tx: Transaction):
    return await get_record(*tx, "units", record_id)


@router.post("/units", response_model=s.UnitRead, status_code=201, operation_id="createUnit")
async def create_units(body: s.UnitCreate, tx: Transaction, key: Key):
    return await write_command(*tx, "units", body.model_dump(), key)


@router.put("/units/{record_id}", response_model=s.UnitRead, operation_id="updateUnit")
async def update_units(record_id: UUID, body: s.UnitUpdate, tx: Transaction, key: Key):
    return await write_command(
        *tx,
        "units",
        body.model_dump(exclude={"expected_version"}),
        key,
        record_id,
        body.expected_version,
    )


@router.post("/units/{record_id}/activate", response_model=s.UnitRead, operation_id="activateUnit")
async def activate_units(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(*tx, "units", {}, key, record_id, body.expected_version, active=True)


@router.post(
    "/units/{record_id}/deactivate", response_model=s.UnitRead, operation_id="deactivateUnit"
)
async def deactivate_units(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(
        *tx, "units", {}, key, record_id, body.expected_version, active=False
    )


@router.get("/products", response_model=s.Page[s.ProductRead], operation_id="listProduct")
async def list_products(
    tx: Transaction,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    active: bool | None = None,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    brand_id: UUID | None = None,
    supplier_id: UUID | None = None,
):
    return await list_records(
        *tx,
        "products",
        q,
        page,
        page_size,
        active,
        {
            "product_id": product_id,
            "category_id": category_id,
            "brand_id": brand_id,
            "supplier_id": supplier_id,
        },
    )


@router.get("/products/{record_id}", response_model=s.ProductRead, operation_id="getProduct")
async def get_products(record_id: UUID, tx: Transaction):
    return await get_record(*tx, "products", record_id)


@router.post(
    "/products", response_model=s.ProductRead, status_code=201, operation_id="createProduct"
)
async def create_products(body: s.ProductCreate, tx: Transaction, key: Key):
    return await write_command(*tx, "products", body.model_dump(), key)


@router.put("/products/{record_id}", response_model=s.ProductRead, operation_id="updateProduct")
async def update_products(record_id: UUID, body: s.ProductUpdate, tx: Transaction, key: Key):
    return await write_command(
        *tx,
        "products",
        body.model_dump(exclude={"expected_version"}),
        key,
        record_id,
        body.expected_version,
    )


@router.post(
    "/products/{record_id}/activate", response_model=s.ProductRead, operation_id="activateProduct"
)
async def activate_products(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(
        *tx, "products", {}, key, record_id, body.expected_version, active=True
    )


@router.post(
    "/products/{record_id}/deactivate",
    response_model=s.ProductRead,
    operation_id="deactivateProduct",
)
async def deactivate_products(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(
        *tx, "products", {}, key, record_id, body.expected_version, active=False
    )


@router.get(
    "/product-units", response_model=s.Page[s.ProductUnitRead], operation_id="listProductUnit"
)
async def list_product_units(
    tx: Transaction,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    active: bool | None = None,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    brand_id: UUID | None = None,
    supplier_id: UUID | None = None,
):
    return await list_records(
        *tx,
        "product-units",
        q,
        page,
        page_size,
        active,
        {
            "product_id": product_id,
            "category_id": category_id,
            "brand_id": brand_id,
            "supplier_id": supplier_id,
        },
    )


@router.get(
    "/product-units/{record_id}", response_model=s.ProductUnitRead, operation_id="getProductUnit"
)
async def get_product_units(record_id: UUID, tx: Transaction):
    return await get_record(*tx, "product-units", record_id)


@router.post(
    "/product-units",
    response_model=s.ProductUnitRead,
    status_code=201,
    operation_id="createProductUnit",
)
async def create_product_units(body: s.ProductUnitCreate, tx: Transaction, key: Key):
    return await write_command(*tx, "product-units", body.model_dump(), key)


@router.put(
    "/product-units/{record_id}", response_model=s.ProductUnitRead, operation_id="updateProductUnit"
)
async def update_product_units(
    record_id: UUID, body: s.ProductUnitUpdate, tx: Transaction, key: Key
):
    return await write_command(
        *tx,
        "product-units",
        body.model_dump(exclude={"expected_version"}),
        key,
        record_id,
        body.expected_version,
    )


@router.post(
    "/product-units/{record_id}/activate",
    response_model=s.ProductUnitRead,
    operation_id="activateProductUnit",
)
async def activate_product_units(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(
        *tx, "product-units", {}, key, record_id, body.expected_version, active=True
    )


@router.post(
    "/product-units/{record_id}/deactivate",
    response_model=s.ProductUnitRead,
    operation_id="deactivateProductUnit",
)
async def deactivate_product_units(
    record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key
):
    return await write_command(
        *tx, "product-units", {}, key, record_id, body.expected_version, active=False
    )


@router.get(
    "/product-prices", response_model=s.Page[s.ProductPriceRead], operation_id="listProductPrice"
)
async def list_product_prices(
    tx: Transaction,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    active: bool | None = None,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    brand_id: UUID | None = None,
    supplier_id: UUID | None = None,
):
    return await list_records(
        *tx,
        "product-prices",
        q,
        page,
        page_size,
        active,
        {
            "product_id": product_id,
            "category_id": category_id,
            "brand_id": brand_id,
            "supplier_id": supplier_id,
        },
    )


@router.get(
    "/product-prices/{record_id}", response_model=s.ProductPriceRead, operation_id="getProductPrice"
)
async def get_product_prices(record_id: UUID, tx: Transaction):
    return await get_record(*tx, "product-prices", record_id)


@router.post(
    "/product-prices",
    response_model=s.ProductPriceRead,
    status_code=201,
    operation_id="createProductPrice",
)
async def create_product_prices(body: s.ProductPriceCreate, tx: Transaction, key: Key):
    return await write_command(*tx, "product-prices", body.model_dump(), key)


@router.put(
    "/product-prices/{record_id}",
    response_model=s.ProductPriceRead,
    operation_id="updateProductPrice",
)
async def update_product_prices(
    record_id: UUID, body: s.ProductPriceUpdate, tx: Transaction, key: Key
):
    return await write_command(
        *tx,
        "product-prices",
        body.model_dump(exclude={"expected_version"}),
        key,
        record_id,
        body.expected_version,
    )


@router.post(
    "/product-prices/{record_id}/activate",
    response_model=s.ProductPriceRead,
    operation_id="activateProductPrice",
)
async def activate_product_prices(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(
        *tx, "product-prices", {}, key, record_id, body.expected_version, active=True
    )


@router.post(
    "/product-prices/{record_id}/deactivate",
    response_model=s.ProductPriceRead,
    operation_id="deactivateProductPrice",
)
async def deactivate_product_prices(
    record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key
):
    return await write_command(
        *tx, "product-prices", {}, key, record_id, body.expected_version, active=False
    )


@router.get("/customers", response_model=s.Page[s.CustomerRead], operation_id="listCustomer")
async def list_customers(
    tx: Transaction,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    active: bool | None = None,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    brand_id: UUID | None = None,
    supplier_id: UUID | None = None,
):
    return await list_records(
        *tx,
        "customers",
        q,
        page,
        page_size,
        active,
        {
            "product_id": product_id,
            "category_id": category_id,
            "brand_id": brand_id,
            "supplier_id": supplier_id,
        },
    )


@router.get("/customers/{record_id}", response_model=s.CustomerRead, operation_id="getCustomer")
async def get_customers(record_id: UUID, tx: Transaction):
    return await get_record(*tx, "customers", record_id)


@router.post(
    "/customers", response_model=s.CustomerRead, status_code=201, operation_id="createCustomer"
)
async def create_customers(body: s.CustomerCreate, tx: Transaction, key: Key):
    return await write_command(*tx, "customers", body.model_dump(), key)


@router.put("/customers/{record_id}", response_model=s.CustomerRead, operation_id="updateCustomer")
async def update_customers(record_id: UUID, body: s.CustomerUpdate, tx: Transaction, key: Key):
    return await write_command(
        *tx,
        "customers",
        body.model_dump(exclude={"expected_version"}),
        key,
        record_id,
        body.expected_version,
    )


@router.post(
    "/customers/{record_id}/activate",
    response_model=s.CustomerRead,
    operation_id="activateCustomer",
)
async def activate_customers(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(
        *tx, "customers", {}, key, record_id, body.expected_version, active=True
    )


@router.post(
    "/customers/{record_id}/deactivate",
    response_model=s.CustomerRead,
    operation_id="deactivateCustomer",
)
async def deactivate_customers(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(
        *tx, "customers", {}, key, record_id, body.expected_version, active=False
    )


@router.get("/suppliers", response_model=s.Page[s.SupplierRead], operation_id="listSupplier")
async def list_suppliers(
    tx: Transaction,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    active: bool | None = None,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    brand_id: UUID | None = None,
    supplier_id: UUID | None = None,
):
    return await list_records(
        *tx,
        "suppliers",
        q,
        page,
        page_size,
        active,
        {
            "product_id": product_id,
            "category_id": category_id,
            "brand_id": brand_id,
            "supplier_id": supplier_id,
        },
    )


@router.get("/suppliers/{record_id}", response_model=s.SupplierRead, operation_id="getSupplier")
async def get_suppliers(record_id: UUID, tx: Transaction):
    return await get_record(*tx, "suppliers", record_id)


@router.post(
    "/suppliers", response_model=s.SupplierRead, status_code=201, operation_id="createSupplier"
)
async def create_suppliers(body: s.SupplierCreate, tx: Transaction, key: Key):
    return await write_command(*tx, "suppliers", body.model_dump(), key)


@router.put("/suppliers/{record_id}", response_model=s.SupplierRead, operation_id="updateSupplier")
async def update_suppliers(record_id: UUID, body: s.SupplierUpdate, tx: Transaction, key: Key):
    return await write_command(
        *tx,
        "suppliers",
        body.model_dump(exclude={"expected_version"}),
        key,
        record_id,
        body.expected_version,
    )


@router.post(
    "/suppliers/{record_id}/activate",
    response_model=s.SupplierRead,
    operation_id="activateSupplier",
)
async def activate_suppliers(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(
        *tx, "suppliers", {}, key, record_id, body.expected_version, active=True
    )


@router.post(
    "/suppliers/{record_id}/deactivate",
    response_model=s.SupplierRead,
    operation_id="deactivateSupplier",
)
async def deactivate_suppliers(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(
        *tx, "suppliers", {}, key, record_id, body.expected_version, active=False
    )


@router.get(
    "/supplier-products",
    response_model=s.Page[s.SupplierProductRead],
    operation_id="listSupplierProduct",
)
async def list_supplier_products(
    tx: Transaction,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    active: bool | None = None,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    brand_id: UUID | None = None,
    supplier_id: UUID | None = None,
):
    return await list_records(
        *tx,
        "supplier-products",
        q,
        page,
        page_size,
        active,
        {
            "product_id": product_id,
            "category_id": category_id,
            "brand_id": brand_id,
            "supplier_id": supplier_id,
        },
    )


@router.get(
    "/supplier-products/{record_id}",
    response_model=s.SupplierProductRead,
    operation_id="getSupplierProduct",
)
async def get_supplier_products(record_id: UUID, tx: Transaction):
    return await get_record(*tx, "supplier-products", record_id)


@router.post(
    "/supplier-products",
    response_model=s.SupplierProductRead,
    status_code=201,
    operation_id="createSupplierProduct",
)
async def create_supplier_products(body: s.SupplierProductCreate, tx: Transaction, key: Key):
    return await write_command(*tx, "supplier-products", body.model_dump(), key)


@router.put(
    "/supplier-products/{record_id}",
    response_model=s.SupplierProductRead,
    operation_id="updateSupplierProduct",
)
async def update_supplier_products(
    record_id: UUID, body: s.SupplierProductUpdate, tx: Transaction, key: Key
):
    return await write_command(
        *tx,
        "supplier-products",
        body.model_dump(exclude={"expected_version"}),
        key,
        record_id,
        body.expected_version,
    )


@router.post(
    "/supplier-products/{record_id}/activate",
    response_model=s.SupplierProductRead,
    operation_id="activateSupplierProduct",
)
async def activate_supplier_products(
    record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key
):
    return await write_command(
        *tx, "supplier-products", {}, key, record_id, body.expected_version, active=True
    )


@router.post(
    "/supplier-products/{record_id}/deactivate",
    response_model=s.SupplierProductRead,
    operation_id="deactivateSupplierProduct",
)
async def deactivate_supplier_products(
    record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key
):
    return await write_command(
        *tx, "supplier-products", {}, key, record_id, body.expected_version, active=False
    )


@router.get("/warehouses", response_model=s.Page[s.WarehouseRead], operation_id="listWarehouse")
async def list_warehouses(
    tx: Transaction,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    active: bool | None = None,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    brand_id: UUID | None = None,
    supplier_id: UUID | None = None,
):
    return await list_records(
        *tx,
        "warehouses",
        q,
        page,
        page_size,
        active,
        {
            "product_id": product_id,
            "category_id": category_id,
            "brand_id": brand_id,
            "supplier_id": supplier_id,
        },
    )


@router.get("/warehouses/{record_id}", response_model=s.WarehouseRead, operation_id="getWarehouse")
async def get_warehouses(record_id: UUID, tx: Transaction):
    return await get_record(*tx, "warehouses", record_id)


@router.post(
    "/warehouses", response_model=s.WarehouseRead, status_code=201, operation_id="createWarehouse"
)
async def create_warehouses(body: s.WarehouseCreate, tx: Transaction, key: Key):
    return await write_command(*tx, "warehouses", body.model_dump(), key)


@router.put(
    "/warehouses/{record_id}", response_model=s.WarehouseRead, operation_id="updateWarehouse"
)
async def update_warehouses(record_id: UUID, body: s.WarehouseUpdate, tx: Transaction, key: Key):
    return await write_command(
        *tx,
        "warehouses",
        body.model_dump(exclude={"expected_version"}),
        key,
        record_id,
        body.expected_version,
    )


@router.post(
    "/warehouses/{record_id}/activate",
    response_model=s.WarehouseRead,
    operation_id="activateWarehouse",
)
async def activate_warehouses(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(
        *tx, "warehouses", {}, key, record_id, body.expected_version, active=True
    )


@router.post(
    "/warehouses/{record_id}/deactivate",
    response_model=s.WarehouseRead,
    operation_id="deactivateWarehouse",
)
async def deactivate_warehouses(record_id: UUID, body: s.VersionInput, tx: Transaction, key: Key):
    return await write_command(
        *tx, "warehouses", {}, key, record_id, body.expected_version, active=False
    )
