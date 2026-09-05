"""Bounded catalog previews and permanent per-row import results."""

from pathlib import Path

from alembic import op

revision = "0013_catalog_import"
down_revision = "0012_funds"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Successful import receipts are permanent; use a forward migration")
