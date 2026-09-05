"""Operating overview read permission and bounded reporting access paths."""

from pathlib import Path

from alembic import op

revision = "0015_reporting"
down_revision = "0014_replenishment"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Business facts are append-only; use a forward migration")
