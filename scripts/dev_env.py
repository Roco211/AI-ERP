"""Generate untracked local credentials; never overwrite an existing .env."""
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".env"
if target.exists():
    raise SystemExit(".env already exists; kept unchanged")
template = (root / ".env.example").read_text()
template = template.replace("APP_DATABASE_PASSWORD", secrets.token_hex(24))
template = template.replace("MIGRATION_DATABASE_PASSWORD", secrets.token_hex(24))
template = template.replace("GENERATE_AT_LEAST_32_RANDOM_CHARACTERS", secrets.token_hex(32))
template = template.replace("SET_A_LOCAL_PASSWORD_AT_LEAST_12_CHARACTERS", secrets.token_urlsafe(20))
target.write_text(template)
target.chmod(0o600)
print("Created .env with local credentials. Read SEED_ADMIN_* locally to sign in.")
