"""Explicit funds cutover, immutable sources, cash and allocations."""

from pathlib import Path

from alembic import op

revision = "0012_funds"
down_revision = "0011_sales_returns"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Business facts are append-only; use a forward migration")
