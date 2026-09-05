"""Permanent replenishment purchase draft provenance and deduplication."""

from pathlib import Path

from alembic import op

revision = "0014_replenishment"
down_revision = "0013_catalog_import"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Replenishment creation receipts are permanent; use a forward migration")
