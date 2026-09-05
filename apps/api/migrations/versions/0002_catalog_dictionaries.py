"""Tenant-owned category, brand and unit dictionaries."""

from alembic import op

revision = "0002_catalog_dictionaries"
down_revision = "0001_bootstrap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("categories", "brands", "units"):
        extra = (
            ", parent_id uuid, attribute_schema jsonb NOT NULL DEFAULT '[]'"
            if table == "categories"
            else ""
        )
        op.execute(f"""CREATE TABLE forge.{table} (
            id uuid PRIMARY KEY DEFAULT uuidv7(),
            organization_id uuid NOT NULL REFERENCES forge.organizations(id),
            code text NOT NULL, name text NOT NULL, notes text NOT NULL DEFAULT '',
            active boolean NOT NULL DEFAULT true, version integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(organization_id,id), UNIQUE(organization_id,code)
            {extra}
        )""")
        op.execute(f"ALTER TABLE forge.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE forge.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_scope ON forge.{table} TO forge_app
            USING(organization_id=nullif(current_setting('app.organization_id',true),'')::uuid)
            WITH CHECK(organization_id=
                nullif(current_setting('app.organization_id',true),'')::uuid)""")
        op.execute(f"GRANT SELECT,INSERT,UPDATE ON forge.{table} TO forge_app")
    op.execute("""ALTER TABLE forge.categories ADD CONSTRAINT category_parent_tenant
        FOREIGN KEY(organization_id,parent_id) REFERENCES forge.categories(organization_id,id)""")
    for permission in (
        "catalog.read",
        "catalog.write",
        "product.price.read",
        "product.price.write",
        "customer.read",
        "customer.write",
        "supplier.read",
        "supplier.write",
        "warehouse.read",
        "warehouse.write",
    ):
        op.execute(f"INSERT INTO forge.permissions VALUES ('{permission}','{permission}')")
    op.execute("""INSERT INTO forge.role_permissions(organization_id,role_id,permission_code)
        SELECT r.organization_id,r.id,p.code FROM forge.roles r CROSS JOIN forge.permissions p
        WHERE r.code='ADMIN' ON CONFLICT DO NOTHING""")


def downgrade() -> None:
    for table in ("categories", "brands", "units"):
        op.execute(f"DROP TABLE forge.{table}")
    op.execute("""DELETE FROM forge.role_permissions WHERE permission_code LIKE 'catalog.%'
        OR permission_code LIKE 'product.price.%' OR permission_code LIKE 'customer.%'
        OR permission_code LIKE 'supplier.%' OR permission_code LIKE 'warehouse.%'""")
    op.execute("""DELETE FROM forge.permissions WHERE code LIKE 'catalog.%'
        OR code LIKE 'product.price.%' OR code LIKE 'customer.%'
        OR code LIKE 'supplier.%' OR code LIKE 'warehouse.%'""")
