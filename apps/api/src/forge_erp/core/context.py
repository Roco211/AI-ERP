from dataclasses import dataclass
from uuid import UUID

from forge_erp.core.errors import Problem


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    organization_id: UUID
    user_id: UUID
    permissions: frozenset[str]
    request_id: str
    source: str = "WEB"
    conversation_id: UUID | None = None

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise Problem(403, "PERMISSION_DENIED", "Permission denied")
