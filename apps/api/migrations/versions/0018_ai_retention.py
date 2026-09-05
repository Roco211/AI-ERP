"""Bounded discovery and erasure of expired private assistant bodies."""

from pathlib import Path

from alembic import op

revision = "0018_ai_retention"
down_revision = "0017_ai_provider_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Business facts are append-only; use a forward migration")
