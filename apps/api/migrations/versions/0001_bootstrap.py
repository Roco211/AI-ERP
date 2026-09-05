"""Bootstrap identity, tenancy, and transactional foundations."""

from pathlib import Path

from alembic import op

revision = "0001_bootstrap"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade() -> None:
    # Destructive: development databases only. Production migrations are append-only.
    op.execute("DROP SCHEMA forge CASCADE")
