"""Transaction visibility watermark for stable immutable-ledger pagination."""

from alembic import op

revision = "0006_inventory_snapshot"
down_revision = "0005_inventory"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE forge.inventory_movements ADD COLUMN created_xid "
        "xid8 NOT NULL DEFAULT pg_current_xact_id()"
    )


def downgrade():
    op.execute("ALTER TABLE forge.inventory_movements DROP COLUMN created_xid")
