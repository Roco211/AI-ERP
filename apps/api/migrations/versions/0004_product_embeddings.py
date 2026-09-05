"""Versioned tenant-scoped product vector projection."""

from alembic import op

revision = "0004_product_embeddings"
down_revision = "0003_catalog_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE forge.product_embeddings (
        organization_id uuid NOT NULL,
        product_id uuid NOT NULL,
        product_version integer NOT NULL,
        model_identity text NOT NULL,
        embedding vector(1024) NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(organization_id,product_id),
        FOREIGN KEY(organization_id,product_id) REFERENCES forge.products(organization_id,id)
    )""")
    op.execute("ALTER TABLE forge.product_embeddings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE forge.product_embeddings FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_scope ON forge.product_embeddings TO forge_app
        USING(organization_id=nullif(current_setting('app.organization_id',true),'')::uuid)
        WITH CHECK(organization_id=nullif(current_setting('app.organization_id',true),'')::uuid)
    """)
    op.execute("GRANT SELECT,INSERT,UPDATE ON forge.product_embeddings TO forge_app")


def downgrade() -> None:
    op.execute("DROP TABLE forge.product_embeddings")
