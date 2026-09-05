"""Catalog products, contacts, units, prices and warehouse metadata."""

from alembic import op

revision = "0003_catalog_records"
down_revision = "0002_catalog_dictionaries"
branch_labels = None
depends_on = None

TABLES = {
    "customers": """
        code text NOT NULL, name text NOT NULL, contact text NOT NULL DEFAULT '', phone text
        NOT NULL DEFAULT '', email text NOT NULL DEFAULT '', address text NOT NULL DEFAULT '',
        price_tier text NOT NULL DEFAULT 'standard' CHECK(price_tier IN
        ('standard','retail','wholesale')), UNIQUE(organization_id,code)""",
    "suppliers": """
        code text NOT NULL, name text NOT NULL, contact text NOT NULL DEFAULT '', phone text
        NOT NULL DEFAULT '', email text NOT NULL DEFAULT '', address text NOT NULL DEFAULT '',
        UNIQUE(organization_id,code)""",
    "warehouses": """
        code text NOT NULL, name text NOT NULL, address text NOT NULL DEFAULT '',
        UNIQUE(organization_id,code)""",
    "products": """
        sku text NOT NULL, barcode text, name text NOT NULL, short_name text NOT NULL DEFAULT
        '', category_id uuid NOT NULL, brand_id uuid, model text NOT NULL DEFAULT '',
        specification text NOT NULL DEFAULT '', attributes jsonb NOT NULL DEFAULT '{}',
        base_unit_id uuid NOT NULL, default_purchase_unit_id uuid NOT NULL,
        default_sales_unit_id uuid NOT NULL, min_stock_qty numeric(20,6) NOT NULL DEFAULT 0
        CHECK(min_stock_qty>=0), reorder_qty numeric(20,6) NOT NULL DEFAULT 0
        CHECK(reorder_qty>=0), preferred_supplier_id uuid, search_text text NOT NULL DEFAULT
        '', UNIQUE(organization_id,sku), UNIQUE(organization_id,barcode)""",
    "product_units": """
        product_id uuid NOT NULL, unit_id uuid NOT NULL, unit_to_base_factor numeric(20,6) NOT
        NULL CHECK(unit_to_base_factor>0), UNIQUE(organization_id,product_id,unit_id)""",
    "product_prices": """
        product_id uuid NOT NULL, price_type text NOT NULL CHECK(price_type IN
        ('standard','retail','wholesale','customer')), customer_id uuid, price numeric(20,6)
        NOT NULL CHECK(price>=0), CHECK((price_type='customer')=(customer_id IS NOT NULL)),
        UNIQUE NULLS NOT DISTINCT(organization_id,product_id,price_type,customer_id)""",
    "supplier_products": """
        supplier_id uuid NOT NULL, product_id uuid NOT NULL, supplier_sku text NOT NULL,
        purchase_unit_id uuid NOT NULL, lead_days integer NOT NULL DEFAULT 0
        CHECK(lead_days>=0), UNIQUE(organization_id,supplier_id,supplier_sku),
        UNIQUE(organization_id,supplier_id,product_id)""",
}
REFERENCES = {
    "products": {
        "category_id": "categories",
        "brand_id": "brands",
        "base_unit_id": "units",
        "default_purchase_unit_id": "units",
        "default_sales_unit_id": "units",
        "preferred_supplier_id": "suppliers",
    },
    "product_units": {"product_id": "products", "unit_id": "units"},
    "product_prices": {"product_id": "products", "customer_id": "customers"},
    "supplier_products": {
        "supplier_id": "suppliers",
        "product_id": "products",
        "purchase_unit_id": "units",
    },
}


def upgrade() -> None:
    for table, fields in TABLES.items():
        op.execute(f"""CREATE TABLE forge.{table} (
            id uuid PRIMARY KEY DEFAULT uuidv7(),
            organization_id uuid NOT NULL REFERENCES forge.organizations(id),
            active boolean NOT NULL DEFAULT true, version integer NOT NULL DEFAULT 1,
            notes text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(organization_id,id), {fields})""")
        op.execute(f"ALTER TABLE forge.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE forge.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_scope ON forge.{table} TO forge_app
            USING(organization_id=nullif(current_setting('app.organization_id',true),'')::uuid)
            WITH CHECK(organization_id=
                nullif(current_setting('app.organization_id',true),'')::uuid)""")
        op.execute(f"GRANT SELECT,INSERT,UPDATE ON forge.{table} TO forge_app")
    for table, refs in REFERENCES.items():
        for column, target in refs.items():
            op.execute(f"""ALTER TABLE forge.{table} ADD FOREIGN KEY(organization_id,{column})
                REFERENCES forge.{target}(organization_id,id)""")
    op.execute("""ALTER TABLE forge.supplier_products
        ADD FOREIGN KEY(organization_id,product_id,purchase_unit_id)
        REFERENCES forge.product_units(organization_id,product_id,unit_id)""")
    op.execute("""CREATE INDEX products_search_trgm ON forge.products
        USING gin(search_text gin_trgm_ops)""")
    op.execute("CREATE INDEX products_attributes_gin ON forge.products USING gin(attributes)")
    op.execute("CREATE INDEX products_category ON forge.products(organization_id,category_id)")
    op.execute(
        "CREATE INDEX product_prices_lookup ON forge.product_prices(organization_id,product_id)"
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE forge.{table}")
