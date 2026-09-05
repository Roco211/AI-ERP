import hashlib
import hmac
import json

from pwdlib import PasswordHash

from forge_erp.core.config import settings

passwords = PasswordHash.recommended()
DUMMY_HASH = passwords.hash("constant-time-invalid-login-placeholder")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def fingerprint(value: object) -> str:
    secret = settings().session_secret
    if len(secret) < 32:
        raise RuntimeError("SESSION_SECRET must contain at least 32 random characters")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
