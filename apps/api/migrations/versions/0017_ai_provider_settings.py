"""Organization-managed encrypted model provider settings."""

from pathlib import Path

from alembic import op

revision = "0017_ai_provider_settings"
down_revision = "0016_ai_assistant"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Preserve provider settings and use a forward migration")
