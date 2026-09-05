"""Purchasing orders and typed inventory document extensions."""

from pathlib import Path

from alembic import op

revision = "0008_purchasing"
down_revision = "0007_inventory_projection"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Business facts are append-only; use a forward migration")
