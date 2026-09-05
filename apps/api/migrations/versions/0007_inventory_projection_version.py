"""Separate immutable ledger sequence from mutable projection revision."""

from alembic import op

revision = "0007_inventory_projection"
down_revision = "0006_inventory_snapshot"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE forge.inventory_balances ADD COLUMN movement_sequence bigint NOT NULL "
        "DEFAULT 0 CHECK(movement_sequence>=0)"
    )
    op.execute("""UPDATE forge.inventory_balances b SET movement_sequence=coalesce(
        (SELECT max(m.sequence) FROM forge.inventory_movements m
        WHERE (m.organization_id,m.warehouse_id,m.product_id)=
        (b.organization_id,b.warehouse_id,b.product_id)),0)""")


def downgrade():
    raise RuntimeError(
        "Projection revisions protect stocktake baselines; migrations are append-only"
    )
