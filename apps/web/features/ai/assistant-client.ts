import { api, ApiError } from "@/lib/api";
import type { components } from "@/generated/api/schema";

export type AssistantStatus = components["schemas"]["AssistantStatus"];
export type Conversation = components["schemas"]["ConversationRead"];
export type ConversationSummary = components["schemas"]["ConversationSummary"];
export type ConversationsPage = components["schemas"]["ConversationsPage"];
export type Turn = components["schemas"]["TurnRead"];
export type Evidence = components["schemas"]["Evidence"];
export type Proposal = components["schemas"]["ProposalRead"];
export type Preview = components["schemas"]["DraftPreview"];
export type DraftInput = components["schemas"]["ProposalEdit"]["draft"];
export type Receipt = components["schemas"]["DraftCreationReceipt"];
export type MessageInput = components["schemas"]["MessageInput"];
export type ProposalEdit = components["schemas"]["ProposalEdit"];
export type ProposalApproval = components["schemas"]["ProposalApproval"];

export class AssistantError extends ApiError {
  constructor(status: number, message: string, public code?: string, requestId?: string) {
    super(status, message, requestId);
  }
}

type ResponseResult<T> = { data?: T; error?: unknown; response: Response };
function result<T>({ data, error, response }: ResponseResult<T>): T {
  if (!response.ok || data === undefined) {
    const problem = error as components["schemas"]["ProblemDetails"] | undefined;
    throw new AssistantError(response.status, problem?.detail ?? "暂时无法完成，请稍后重试。",
      problem?.code, problem?.request_id);
  }
  return data;
}
const header = (key: string) => ({ "Idempotency-Key": key });

export async function getAssistantStatus(signal?: AbortSignal) {
  return result(await api.GET("/api/v1/ai/status", { signal }));
}
export async function listConversations(page = 1, signal?: AbortSignal) {
  return result(await api.GET("/api/v1/ai/conversations", {
    params: { query: { page, page_size: 20 } }, signal,
  }));
}
export async function getConversation(id: string, signal?: AbortSignal) {
  return result(await api.GET("/api/v1/ai/conversations/{id}", {
    params: { path: { id } }, signal,
  }));
}
export async function createConversation(title: string, key: string) {
  return result(await api.POST("/api/v1/ai/conversations", {
    params: { header: header(key) }, body: { title },
  }));
}
export async function deleteConversation(id: string, key: string) {
  return result(await api.DELETE("/api/v1/ai/conversations/{id}", {
    params: { path: { id }, header: header(key) },
  }));
}
export async function sendMessage(id: string, body: MessageInput, key: string) {
  return result(await api.POST("/api/v1/ai/conversations/{id}/messages", {
    params: { path: { id }, header: header(key) }, body,
  }));
}
export async function requestBrief(id: string, day: string | undefined, key: string) {
  return result(await api.POST("/api/v1/ai/conversations/{id}/brief", {
    params: { path: { id }, header: header(key) }, body: day ? { day } : {},
  }));
}
export async function retryTurn(turnId: string, key: string) {
  return result(await api.POST("/api/v1/ai/turns/{turn_id}/retry", {
    params: { path: { turn_id: turnId }, header: header(key) },
  }));
}
export async function editProposal(id: string, body: ProposalEdit, key: string) {
  return result(await api.POST("/api/v1/ai/proposals/{id}/preview", {
    params: { path: { id }, header: header(key) }, body,
  }));
}
export async function approveProposal(id: string, body: ProposalApproval, key: string) {
  return result(await api.POST("/api/v1/ai/proposals/{id}/approve", {
    params: { path: { id }, header: header(key) }, body,
  }));
}
export async function rejectProposal(id: string, revision: number, key: string) {
  return result(await api.POST("/api/v1/ai/proposals/{id}/reject", {
    params: { path: { id }, header: header(key) }, body: { expected_revision: revision },
  }));
}

/** Revalidate links even though they are server-authored; never render model URLs. */
export function safeSourceHref(value: string): boolean {
  if (!value.startsWith("/") || value.startsWith("//") || /[\\\u0000-\u0020]/.test(value)) return false;
  const routes: Record<string, readonly string[]> = {
    "/products": [], "/customers": [], "/suppliers": [], "/settings/units": [],
    "/settings/warehouses": [], "/inventory": ["document", "tab"], "/sales": ["order", "document"],
    "/purchase": ["order", "document"], "/funds": ["source", "side"], "/dashboard": [],
    "/replenishment": [],
  };
  try {
    const parsed = new URL(value, "https://forge.invalid");
    if (parsed.origin !== "https://forge.invalid" || parsed.hash || !(parsed.pathname in routes)) return false;
    const seen = new Set<string>();
    for (const [key, item] of parsed.searchParams) {
      if (seen.has(key) || !routes[parsed.pathname].includes(key)) return false;
      seen.add(key);
      if (key === "side" ? !["AR", "AP"].includes(item)
        : key === "tab" ? item !== "movements"
        : !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(item)) return false;
    }
    return true;
  } catch { return false; }
}
