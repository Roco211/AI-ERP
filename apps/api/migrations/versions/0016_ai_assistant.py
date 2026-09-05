"""Owner-scoped AI progress and permanent reviewed-draft receipts."""

from pathlib import Path

from alembic import op

revision = "0016_ai_assistant"
down_revision = "0015_reporting"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Business facts are append-only; use a forward migration")
