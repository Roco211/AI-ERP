import base64
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import SecretStr

from forge_erp.core.config import settings
from forge_erp.core.errors import Problem


def cipher(organization_id: UUID) -> Fernet:
    secret = settings().session_secret
    if len(secret) < 32:
        raise Problem(503, "AI_ENCRYPTION_UNAVAILABLE", "服务端密钥配置不完整")
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=organization_id.bytes,
        info=b"forge-erp-ai-provider-credential-v1",
    ).derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_key(organization_id: UUID, key: SecretStr) -> str:
    return cipher(organization_id).encrypt(key.get_secret_value().encode()).decode()


def decrypt_key(organization_id: UUID, encrypted: str | None) -> SecretStr:
    if encrypted is None:
        return SecretStr("")
    try:
        return SecretStr(cipher(organization_id).decrypt(encrypted.encode()).decode())
    except InvalidToken, ValueError, UnicodeError:
        raise Problem(
            503, "AI_CREDENTIAL_UNREADABLE", "模型密钥无法读取，请在模型设置中重新保存"
        ) from None
