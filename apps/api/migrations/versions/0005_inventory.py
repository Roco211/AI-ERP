"""Inventory ledger, projections, documents, and database immutability."""

from pathlib import Path

from alembic import op

revision = "0005_inventory"
down_revision = "0004_product_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade() -> None:
    raise RuntimeError("Inventory facts are append-only; restore a reviewed backup instead")
