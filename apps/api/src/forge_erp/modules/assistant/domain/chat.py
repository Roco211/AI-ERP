from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from forge_erp.modules.assistant.domain.drafts import DraftInput, DraftPreview
from forge_erp.modules.assistant.domain.tools import Evidence
from forge_erp.modules.catalog.domain.schemas import InputModel


class AssistantStatus(BaseModel):
    configured: bool
    provider_name: str | None = None
    model: str | None = None
    can_manage_provider: bool


class ConversationInput(InputModel):
    title: Annotated[str, Field(min_length=1, max_length=120)] = "新对话"


class MessageInput(InputModel):
    message: Annotated[str, Field(min_length=1, max_length=4000)]
    selected_ids: Annotated[list[UUID], Field(max_length=20)] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入问题")
        return value.strip()


class BriefInput(InputModel):
    day: date | None = None


class DraftCreationReceipt(BaseModel):
    id: UUID
    status: Literal["DRAFT"]
    version: int
    request_id: str
    proposal_id: UUID
    href: str


class ProposalRead(BaseModel):
    id: UUID
    turn_id: UUID
    kind: Literal["SALES", "PURCHASE"]
    revision: int
    status: Literal["PENDING", "CREATED", "REJECTED", "EXPIRED"]
    expires_at: datetime
    preview: DraftPreview | None = None
    receipt: DraftCreationReceipt | None = None


class ProposalEdit(InputModel):
    expected_revision: Annotated[int, Field(ge=1)]
    draft: DraftInput


class ProposalApproval(InputModel):
    expected_revision: Annotated[int, Field(ge=1)]
    confirmation_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ProposalRejection(InputModel):
    expected_revision: Annotated[int, Field(ge=1)]


class Activity(BaseModel):
    id: Annotated[str, Field(max_length=80)]
    kind: Literal["model", "query", "draft", "reply"]
    title: Annotated[str, Field(max_length=120)]
    state: Literal["running", "complete", "failed"]
    started_at: datetime
    finished_at: datetime | None = None


class TurnRead(BaseModel):
    id: UUID
    state: Literal["RUNNING", "WAITING", "COMPLETED", "FAILED"]
    prompt: str
    created_at: datetime
    answer: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    proposal: ProposalRead | None = None
    error_code: str | None = None
    error_message: str | None = None
    model_calls: int
    tool_calls: int
    can_retry: bool
    activity: Annotated[list[Activity], Field(max_length=32)] = Field(default_factory=list)
    interaction: Literal["business", "casual"] = "business"
    guided: bool = False


class AssistantStreamEvent(BaseModel):
    type: Literal["snapshot", "delta", "complete", "error"]
    turn: TurnRead | None = None
    turn_id: UUID | None = None
    delta: Annotated[str, Field(max_length=2000)] | None = None
    status: int | None = None
    code: str | None = None
    detail: str | None = None
    request_id: str | None = None


class ConversationSummary(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    expires_at: datetime


class ConversationsPage(BaseModel):
    items: list[ConversationSummary]
    total: int
    page: int
    page_size: int


class ConversationRead(ConversationSummary):
    turns: list[TurnRead]
    older_turns_omitted: bool = False


class ConversationDeleted(BaseModel):
    id: UUID
    deleted: bool = True
