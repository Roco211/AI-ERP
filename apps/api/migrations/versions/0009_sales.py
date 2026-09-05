"""Sales orders, pricing snapshots and reservation sources."""

from pathlib import Path

from alembic import op

revision = "0009_sales"
down_revision = "0008_purchasing"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Business facts are append-only; use a forward migration")
