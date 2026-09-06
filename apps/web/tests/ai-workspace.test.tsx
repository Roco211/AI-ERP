import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi, type Mock } from "vitest";
import { AssistantWorkspace } from "@/features/ai/workspace";
import { safeSourceHref } from "@/features/ai/assistant-client";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn() },
  ApiError: class extends Error { constructor(public status: number, message: string) { super(message); } },
}));
vi.mock("@/features/catalog/picker", () => ({ ProductPicker: () => <div>快捷选品组件</div> }));
type Options = { body?: Record<string, unknown>; params?: { path?: Record<string, string>;
  query?: Record<string, unknown>; header?: Record<string, string> } };
type Result = { data?: unknown; error?: unknown; response: Response };
const get = api.GET as unknown as Mock<(path: string, options?: Options) => Promise<Result>>;
const post = api.POST as unknown as Mock<(path: string, options?: Options) => Promise<Result>>;
const ok = (data: unknown): Result => ({ data, response: new Response() });
const id = "10000000-0000-4000-8000-000000000001";
const documentId = "20000000-0000-4000-8000-000000000002";
const hash = "a".repeat(64);
const full = ["ai.use", "ai.draft.create", "sales.order.write", "product.price.read",
  "product.cost.read", "inventory.read", "customer.read", "warehouse.read", "catalog.read", "dashboard.read", "sales.read", "purchase.read"];
const status = { configured: true, can_manage_provider: false, provider_name: "CommandCode", model: "selected-model" };
const summary = { id, title: "库存问答", created_at: "2026-09-05T10:00:00Z", expires_at: "2099-09-12T10:00:00Z" };
const evidence = { id: "fact-1", tool: "get_inventory", title: "库存余额", as_of: "2026-09-05T10:00:00Z",
  scope: "本次查询的当前库存", summary: [{ label: "可用数量", value: "123.000001", unit: "个" }],
  columns: ["商品", "可用数量"], rows: [{ 商品: "螺栓 <script>bad()</script>", 可用数量: "123.000001" }],
  links: [{ label: "库存来源", href: "/inventory" }, { label: "恶意外链", href: "javascript:alert(1)" }], truncated: true };
const finished = { id: "turn-1", state: "COMPLETED", prompt: "库存多少？", created_at: "2026-09-05T10:00:00Z",
  answer: "已根据当前记录核对。", evidence: [evidence], proposal: null, error_code: null,
  error_message: null, model_calls: 1, tool_calls: 1, can_retry: false };
function conversation(turns: unknown[] = [finished]) { return { ...summary, turns, older_turns_omitted: false }; }
function proposal() {
  return { id: "proposal-1", turn_id: "turn-1", kind: "SALES", revision: 1, status: "PENDING",
    expires_at: "2099-09-12T10:00:00Z", receipt: null,
    preview: { kind: "SALES", party_name: "宏达客户", warehouse_name: "主仓", confirmation_hash: hash,
      total_amount: "45.0000", warnings: ["请核对商品、数量和单价"],
      order: { customer_id: "customer-1", warehouse_id: "warehouse-1", reason: "客户订货",
        lines: [{ product_id: "product-1", unit_id: "unit-1", qty: "3", pricing_mode: "AUTO" }] },
      lines: [{ product_id: "product-1", unit_id: "unit-1", product_label: "M8 · 螺栓", unit_label: "个",
        qty: "3", unit_price: "15.000000", amount: "45.0000", unit_to_base_factor: "1.000000",
        base_qty: "3.000000", conversion_version: 1, pricing_mode: "AUTO", price_source: { source: "standard" } }] } };
}
function mocks(value = conversation(), configuration = status) {
  get.mockImplementation(async (path) => {
    if (path === "/api/v1/ai/status") return ok(configuration);
    if (path === "/api/v1/ai/conversations") return ok({ items: [summary], total: 1, page: 1, page_size: 20 });
    if (path === "/api/v1/ai/conversations/{id}") return ok(value);
    return ok({ items: [], total: 0 });
  });
}
function show(permissions = full) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const node = (current: string[]) => <QueryClientProvider client={qc}><AssistantWorkspace permissions={current} identityKey="owner-1" /></QueryClientProvider>;
  const view = render(node(permissions));
  return { ...view, qc, permissions: (current: string[]) => view.rerender(node(current)) };
}
// Legacy interaction fixtures exercise the new negotiated client via its JSON
// fallback. Incremental delivery and transport failures have separate stream tests.
beforeEach(() => { vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
  const message = url.match(/\/conversations\/([^/]+)\/(messages|brief)$/);
  const retry = url.match(/\/turns\/([^/]+)\/retry$/);
  if (!message && !retry) throw new Error("Unexpected streaming fixture request");
  const response = await post(message ? `/api/v1/ai/conversations/{id}/${message[2]}` : "/api/v1/ai/turns/{turn_id}/retry", {
    params: { path: message ? { id: message[1] } : { turn_id: retry![1] },
      header: { "Idempotency-Key": new Headers(init.headers).get("Idempotency-Key")! } },
    ...(init.body ? { body: JSON.parse(String(init.body)) } : {}),
  });
  return Response.json(response.response.ok ? response.data : response.error, { status: response.response.status });
})); });
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

test("no assistant permission means no data request", () => {
  show([]);
  expect(screen.getByRole("alert")).toHaveTextContent("没有使用 AI 助手的权限");
  expect(get).not.toHaveBeenCalled();
});

test("provider setup is offered only to managers and unconfigured chat cannot send", async () => {
  mocks(conversation(), { ...status, configured: false, can_manage_provider: true });
  show([...full, "ai.provider.manage"]);
  expect(await screen.findByRole("link", { name: "配置模型服务" })).toHaveAttribute("href", "/settings/llm");
  expect(screen.getByRole("button", { name: "发送问题" })).toBeDisabled();
  expect(screen.getByLabelText("你的问题")).toBeDisabled();
});

test("facts retain exact strings, source time, scope and text escaping", async () => {
  mocks(); show();
  const card = await screen.findByRole("region", { name: "库存余额" });
  expect(card).toHaveTextContent("123.000001");
  expect(card).toHaveTextContent("当前库存");
  expect(card).toHaveTextContent("螺栓 <script>bad()</script>");
  expect(card.querySelector("script")).toBeNull();
  expect(within(card).getByRole("link", { name: "库存来源" })).toHaveAttribute("href", "/inventory");
  expect(screen.queryByRole("link", { name: "恶意外链" })).not.toBeInTheDocument();
  expect(card).toHaveTextContent("只展示部分记录");
});

test("lost message response retries identical original body and key and reloads server history", async () => {
  mocks(); show();
  await screen.findByRole("region", { name: "库存余额" });
  post.mockRejectedValueOnce(new TypeError("connection lost"));
  post.mockResolvedValueOnce(ok({ ...finished, id: "turn-2", prompt: "帮我查 M8" }));
  fireEvent.change(screen.getByLabelText("你的问题"), { target: { value: "帮我查 M8" } });
  const send = screen.getByRole("button", { name: "发送问题" });
  fireEvent.click(send); fireEvent.click(send);
  await screen.findByText(/提交结果待确认/);
  expect(post).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("你的问题")).toBeDisabled();
  expect(screen.getByRole("button", { name: "新对话" })).toBeDisabled();
  const original = post.mock.calls[0];
  expect(original[1]?.body).toEqual({ message: "帮我查 M8", selected_ids: [] });
  fireEvent.click(screen.getByRole("button", { name: "重试原提交" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1]).toEqual(original);
  await waitFor(() => expect(screen.getByLabelText("你的问题")).toHaveValue(""));
  expect(get.mock.calls.filter(([path]) => path === "/api/v1/ai/conversations/{id}").length).toBeGreaterThan(1);
});

test("official composer sends Enter once, while Shift+Enter and IME confirmation do not submit", async () => {
  mocks(); show();
  await screen.findByRole("region", { name: "库存余额" });
  const input = screen.getByRole("textbox", { name: "你的问题" });
  fireEvent.change(input, { target: { value: "查询 M8 库存" } });
  fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  expect(post).not.toHaveBeenCalled();
  expect(input).toHaveValue("查询 M8 库存");
  post.mockRejectedValueOnce(new TypeError("connection lost"));
  fireEvent.keyDown(input, { key: "Enter" });
  fireEvent.keyDown(input, { key: "Enter" });
  await screen.findByText(/提交结果待确认/);
  expect(post).toHaveBeenCalledTimes(1);
  expect(post.mock.calls[0][1]?.body).toEqual({ message: "查询 M8 库存", selected_ids: [] });
  expect(input).toBeDisabled();
  expect(screen.getByRole("button", { name: "发送问题" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: /Stop generating|停止生成|Add to prompt|添加附件/ })).not.toBeInTheDocument();
});

test("the scrollable transcript links named messages to server facts in the context panel", async () => {
  mocks(); show();
  const facts = await screen.findByRole("region", { name: "库存余额" });
  const transcript = screen.getByRole("region", { name: "对话记录" });
  expect(transcript).toHaveAttribute("tabindex", "0");
  expect(within(transcript).getByRole("log")).not.toContainElement(facts);
  expect(within(transcript).getByRole("article", { name: "你的消息" })).toHaveTextContent("库存多少？");
  expect(within(transcript).getByRole("article", { name: "经营助手回复" })).toHaveTextContent("已根据当前记录核对。");
  expect(within(transcript).getByRole("button", { name: /^查看业务依据/ })).toBeEnabled();
  expect(screen.getByRole("complementary", { name: "业务依据与草稿" })).toContainElement(facts);
});

test("authority change immediately hides prior facts and requires new conversation", async () => {
  mocks(); show(); await screen.findByRole("region", { name: "库存余额" });
  post.mockResolvedValueOnce({ error: { code: "AI_AUTHORITY_CHANGED", detail: "权限已变化" }, response: new Response(null, { status: 409 }) });
  fireEvent.change(screen.getByLabelText("你的问题"), { target: { value: "再查一次" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  await screen.findByText(/旧对话已停止展示/);
  expect(screen.queryByRole("region", { name: "库存余额" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "发送问题" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "新对话" })).toBeEnabled();
});

test("revoking assistant permission clears visible conversation", async () => {
  mocks(); const view = show(); await screen.findByRole("region", { name: "库存余额" });
  view.permissions([]);
  expect(screen.queryByText("123.000001")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("你的问题")).not.toBeInTheDocument();
});

test("daily brief uses selected date and no freeform question", async () => {
  mocks(); show(); await screen.findByRole("region", { name: "库存余额" });
  post.mockResolvedValueOnce(ok({ ...finished, id: "brief-1" }));
  fireEvent.click(screen.getByText("每日简报", { exact: true }));
  fireEvent.change(screen.getByLabelText("简报日期"), { target: { value: "2026-09-05" } });
  fireEvent.click(screen.getByRole("button", { name: "生成每日简报" }));
  await waitFor(() => expect(post).toHaveBeenCalled());
  expect(post.mock.calls[0][0]).toBe("/api/v1/ai/conversations/{id}/brief");
  expect(post.mock.calls[0][1]?.body).toEqual({ day: "2026-09-05" });
});

test("failed turn resumes by its server turn id after reload", async () => {
  mocks(conversation([{ ...finished, state: "FAILED", answer: null, can_retry: true, error_message: "服务暂不可用" }]));
  post.mockResolvedValueOnce(ok(finished)); show();
  fireEvent.click(await screen.findByRole("button", { name: "恢复本次处理" }));
  await waitFor(() => expect(post).toHaveBeenCalled());
  expect(post.mock.calls[0][0]).toBe("/api/v1/ai/turns/{turn_id}/retry");
  expect(post.mock.calls[0][1]?.params?.path).toEqual({ turn_id: "turn-1" });
  expect(post.mock.calls[0][1]?.body).toBeUndefined();
});

test("editing a draft hides stale amounts and blocks approval until server preview", async () => {
  const current = proposal();
  mocks(conversation([{ ...finished, state: "WAITING", proposal: current }])); show();
  const editor = await screen.findByRole("region", { name: "开单复核" });
  expect(editor).toHaveTextContent("45.0000");
  fireEvent.change(within(editor).getByLabelText("第 1 行数量"), { target: { value: "2.000001" } });
  expect(within(editor).getByRole("button", { name: "确认创建草稿" })).toBeDisabled();
  expect(within(editor).getByRole("button", { name: "取消此提案" })).toBeEnabled();
  expect(editor).not.toHaveTextContent("服务器核算合计");
  const revised = { ...current, revision: 2, preview: { ...current.preview, total_amount: "30.0001", confirmation_hash: "b".repeat(64) } };
  post.mockImplementationOnce(async () => { mocks(conversation([{ ...finished, state: "WAITING", proposal: revised }])); return ok(revised); });
  fireEvent.click(within(editor).getByRole("button", { name: "重新预览" }));
  await waitFor(() => expect(screen.getByRole("region", { name: "开单复核" })).toHaveTextContent("30.0001"));
  const input = post.mock.calls[0][1]?.body as { expected_revision: number; draft: { order: { lines: Record<string, unknown>[] } } };
  expect(input.expected_revision).toBe(1);
  expect(input.draft.order.lines[0].qty).toBe("2.000001");
  expect(input.draft.order.lines[0]).not.toHaveProperty("unit_price");
  expect(screen.getByRole("button", { name: "确认创建草稿" })).toBeEnabled();
});

test("approval sends only reviewed revision and hash; lost response reuses original request", async () => {
  const current = proposal();
  mocks(conversation([{ ...finished, state: "WAITING", proposal: current }])); show();
  const approve = await screen.findByRole("button", { name: "确认创建草稿" });
  const receipt = { id: documentId, status: "DRAFT", version: 1, request_id: "approval", proposal_id: current.id, href: `/sales?order=${documentId}` };
  post.mockRejectedValueOnce(new TypeError("connection lost")); post.mockResolvedValueOnce(ok(receipt));
  fireEvent.click(approve); await screen.findByText(/提交结果待确认/);
  const original = post.mock.calls[0];
  expect(original[0]).toBe("/api/v1/ai/proposals/{id}/approve");
  expect(original[1]?.body).toEqual({ expected_revision: 1, confirmation_hash: hash });
  fireEvent.click(screen.getByRole("button", { name: "重试原提交" }));
  expect(await screen.findByRole("link", { name: "查看已创建草稿" })).toHaveAttribute("href", receipt.href);
  expect(post.mock.calls[1]).toEqual(original);
});

test("draft submission progress belongs to its approval and original-request retry, not unrelated chat", async () => {
  const current = proposal();
  const draft = { ...finished, state: "WAITING", proposal: current };
  mocks(conversation([draft])); show();
  const editor = await screen.findByRole("region", { name: "开单复核" });
  let finishChat!: (value: Result) => void;
  post.mockImplementationOnce(() => new Promise((resolve) => { finishChat = resolve; }));
  fireEvent.change(screen.getByLabelText("你的问题"), { target: { value: "聊聊今天的安排" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0][0]).toBe("/api/v1/ai/conversations/{id}/messages");
  expect(editor).toHaveTextContent("等待你复核");
  expect(editor).not.toHaveTextContent("正在提交");
  expect(within(editor).getByRole("button", { name: "确认创建草稿" })).toBeDisabled();
  const chat = { ...finished, id: "turn-2", prompt: "聊聊今天的安排", evidence: [], answer: "可以，先聊聊你的想法。" };
  mocks(conversation([draft, chat]));
  await act(async () => { finishChat(ok(chat)); });
  await waitFor(() => expect(within(editor).getByRole("button", { name: "确认创建草稿" })).toBeEnabled());

  let loseApproval!: (error: Error) => void;
  post.mockImplementationOnce(() => new Promise((_resolve, reject) => { loseApproval = reject; }));
  fireEvent.click(within(editor).getByRole("button", { name: "确认创建草稿" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(editor).toHaveTextContent("正在提交");
  const original = post.mock.calls[1];
  expect(original[0]).toBe("/api/v1/ai/proposals/{id}/approve");
  await act(async () => { loseApproval(new TypeError("connection lost")); });
  await screen.findByText(/提交结果待确认/);
  expect(editor).not.toHaveTextContent("正在提交");
  expect(within(editor).getByRole("button", { name: "确认创建草稿" })).toBeDisabled();

  let finishApproval!: (value: Result) => void;
  post.mockImplementationOnce(() => new Promise((resolve) => { finishApproval = resolve; }));
  fireEvent.click(screen.getByRole("button", { name: "重试原提交" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(3));
  expect(editor).toHaveTextContent("正在提交");
  expect(post.mock.calls[2]).toEqual(original);
  const receipt = { id: documentId, status: "DRAFT", version: 1, request_id: "approval", proposal_id: current.id, href: `/sales?order=${documentId}` };
  await act(async () => { finishApproval(ok(receipt)); });
  expect(await screen.findByRole("link", { name: "查看已创建草稿" })).toHaveAttribute("href", receipt.href);
});

test("persisted creation receipt remains visible after a fresh page mount", async () => {
  const current = proposal();
  const receipt = { id: documentId, status: "DRAFT", version: 1, request_id: "saved", proposal_id: current.id, href: `/purchase?order=${documentId}` };
  mocks(conversation([{ ...finished, proposal: { ...current, status: "CREATED", receipt } }]));
  const first = show(); await screen.findByRole("region", { name: "草稿创建结果" }); first.unmount();
  show(); expect(await screen.findByRole("link", { name: "查看已创建草稿" })).toHaveAttribute("href", receipt.href);
  expect(post).not.toHaveBeenCalled();
});

test("source URL validator rejects external URLs and unauthorized routes", () => {
  for (const value of ["https://evil.test", "//evil.test", "javascript:alert(1)", "/api/v1/auth/logout",
    "/sales?order=javascript", "/sales?next=https://evil.test", "/inventory?tab=delete"]) expect(safeSourceHref(value)).toBe(false);
  expect(safeSourceHref(`/sales?document=${documentId}`)).toBe(true);
});

test("running work disables new questions and prior proposal approval until refreshed", async () => {
  mocks(conversation([{ ...finished, state: "RUNNING", proposal: proposal() }])); show();
  expect(await screen.findByText("正在等待助手响应…")).toBeVisible();
  expect(screen.getByRole("button", { name: "发送问题" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "确认创建草稿" })).toBeDisabled();
  mocks(conversation([{ ...finished, state: "WAITING", proposal: proposal() }]));
  fireEvent.click(screen.getByLabelText("对话操作"));
  fireEvent.click(screen.getByRole("button", { name: "重新读取记录" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "确认创建草稿" })).toBeEnabled());
});

test("expiry and missing business permissions never expose an enabled approval action", async () => {
  const current = proposal(); current.expires_at = "2020-01-01T00:00:00Z";
  mocks(conversation([{ ...finished, state: "WAITING", proposal: current }])); show(["ai.use"]);
  expect(await screen.findByText("提案已过期，请重新提出开单请求。")).toBeVisible();
  expect(screen.getByRole("button", { name: "确认创建草稿" })).toBeDisabled();
  fireEvent.click(screen.getByText("每日简报", { exact: true }));
  expect(screen.getByRole("button", { name: "生成每日简报" })).toBeDisabled();
  expect(screen.getByText(/没有创建此类草稿的全部权限/)).toBeVisible();
});

test.each(["ai.draft.create", "sales.order.write", "product.price.read", "catalog.read", "warehouse.read", "customer.read", "sales.read"])("sales proposal requires the complete command authority: %s", async (missing) => {
  mocks(conversation([{ ...finished, state: "WAITING", proposal: proposal() }]));
  show(full.filter((permission) => permission !== missing));
  await screen.findByRole("region", { name: "开单复核" });
  expect(screen.getByRole("button", { name: "确认创建草稿" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "重新预览" })).toBeDisabled();
  expect(post).not.toHaveBeenCalled();
});

test("reject uses its dedicated revision command without sending a model message", async () => {
  const current = proposal();
  mocks(conversation([{ ...finished, state: "WAITING", proposal: current }])); show();
  post.mockImplementationOnce(async () => {
    const rejected = { ...current, status: "REJECTED" };
    mocks(conversation([{ ...finished, proposal: rejected }])); return ok(rejected);
  });
  fireEvent.click(await screen.findByRole("button", { name: "取消此提案" }));
  await screen.findByText("已取消此提案。");
  expect(post.mock.calls[0][0]).toBe("/api/v1/ai/proposals/{id}/reject");
  expect(post.mock.calls[0][1]?.body).toEqual({ expected_revision: 1 });
});

test("expired login hides facts and provides a sign-in path", async () => {
  mocks(); show(); await screen.findByRole("region", { name: "库存余额" });
  post.mockResolvedValueOnce({ error: { code: "UNAUTHENTICATED", detail: "请重新登录" }, response: new Response(null, { status: 401 }) });
  fireEvent.change(screen.getByLabelText("你的问题"), { target: { value: "查询库存" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  expect(await screen.findByRole("link", { name: "重新登录" })).toHaveAttribute("href", "/login");
  expect(screen.queryByRole("region", { name: "库存余额" })).not.toBeInTheDocument();
});

test("new conversation sends only title and remembers its authorized locator for refresh", async () => {
  mocks(); show(); await screen.findByRole("region", { name: "库存余额" });
  post.mockResolvedValueOnce(ok({ ...summary, id: documentId, title: "新对话" }));
  fireEvent.click(screen.getByRole("button", { name: "新对话" }));
  await waitFor(() => expect(new URL(window.location.href).searchParams.get("conversation")).toBe(documentId));
  expect(post.mock.calls[0][0]).toBe("/api/v1/ai/conversations");
  expect(post.mock.calls[0][1]?.body).toEqual({ title: "新对话" });
  expect(post.mock.calls[0][1]?.params?.header?.["Idempotency-Key"]).toBeTruthy();
});

test("purchase preview preserves entered decimal strings and displays only server totals", async () => {
  const original = proposal();
  const current = { ...original, kind: "PURCHASE", preview: { ...original.preview,
    kind: "PURCHASE", party_name: "供应商甲",
    order: { supplier_id: "supplier-1", warehouse_id: "warehouse-1", reason: "备货",
      lines: [{ product_id: "product-1", unit_id: "unit-1", qty: "3", unit_price: "15.000000" }] },
    lines: [{ ...original.preview.lines[0], pricing_mode: "MANUAL", price_source: { source: "manual" } }],
  } };
  mocks(conversation([{ ...finished, state: "WAITING", proposal: current }]));
  show([...full, "purchase.order.write", "supplier.read"]);
  await screen.findByRole("region", { name: "开单复核" });
  expect(screen.queryByLabelText("第 1 行报价方式")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("第 1 行单价"), { target: { value: "12.345678" } });
  fireEvent.change(screen.getByLabelText("第 1 行数量"), { target: { value: "2.000001" } });
  const updated = { ...current, revision: 2, preview: { ...current.preview, total_amount: "24.6914" } };
  post.mockImplementationOnce(async () => {
    mocks(conversation([{ ...finished, state: "WAITING", proposal: updated }])); return ok(updated);
  });
  fireEvent.click(screen.getByRole("button", { name: "重新预览" }));
  await waitFor(() => expect(screen.getByRole("region", { name: "开单复核" })).toHaveTextContent("24.6914"));
  const body = post.mock.calls[0][1]?.body as { draft: { kind: string; order: { lines: Record<string, unknown>[] } } };
  expect(body.draft.kind).toBe("PURCHASE");
  expect(body.draft.order.lines[0]).toEqual({ product_id: "product-1", unit_id: "unit-1", qty: "2.000001", unit_price: "12.345678" });
});

test("selecting another turn preserves an edited draft and its required server re-preview", async () => {
  const current = proposal();
  const draftTurn = { ...finished, state: "WAITING", prompt: "请准备销售草稿", proposal: current };
  const newer = { ...finished, id: "turn-2", prompt: "再查询库存", evidence: [{ ...evidence, id: "fact-2", title: "较新的库存依据" }] };
  mocks(conversation([draftTurn, newer])); show();
  await screen.findByRole("region", { name: "较新的库存依据" });
  const transcript = screen.getByRole("region", { name: "对话记录" });
  fireEvent.click(within(transcript).getByRole("button", { name: "复核草稿" }));
  const editor = await screen.findByRole("region", { name: "开单复核" });
  fireEvent.change(within(editor).getByLabelText("第 1 行数量"), { target: { value: "2.000001" } });
  expect(within(editor).getByRole("button", { name: "确认创建草稿" })).toBeDisabled();
  const input = within(editor).getByLabelText("第 1 行数量");
  fireEvent.click(within(transcript).getAllByRole("button", { name: /^查看业务依据/ }).at(-1)!);
  expect(await screen.findByRole("region", { name: "较新的库存依据" })).toBeVisible();
  expect(screen.queryByRole("region", { name: "开单复核" })).not.toBeInTheDocument();
  expect(input.isConnected).toBe(true);
  fireEvent.click(within(transcript).getByRole("button", { name: "复核草稿" }));
  expect(await screen.findByRole("region", { name: "开单复核" })).toBe(editor);
  expect(within(editor).getByLabelText("第 1 行数量")).toHaveValue("2.000001");
  expect(within(editor).getByRole("button", { name: "确认创建草稿" })).toBeDisabled();
  expect(editor).not.toHaveTextContent("服务器核算合计");
  expect(post).not.toHaveBeenCalled();
});

test("unsent text belongs to its conversation and returns only when that conversation is selected", async () => {
  const second = { ...summary, id: documentId, title: "另一位客户的对话" };
  get.mockImplementation(async (path, options) => {
    if (path === "/api/v1/ai/status") return ok(status);
    if (path === "/api/v1/ai/conversations") return ok({ items: [summary, second], total: 2, page: 1, page_size: 20 });
    if (path === "/api/v1/ai/conversations/{id}") return ok(options?.params?.path?.id === documentId
      ? { ...second, turns: [], older_turns_omitted: false } : conversation());
    return ok({ items: [], total: 0 });
  });
  show(); await screen.findByRole("region", { name: "库存余额" });
  fireEvent.change(screen.getByLabelText("你的问题"), { target: { value: "只给第一个对话的未发送内容" } });
  const history = screen.getByRole("complementary", { name: "历史会话" });
  fireEvent.click(within(history).getByRole("button", { name: /另一位客户的对话/ }));
  await waitFor(() => expect(new URL(window.location.href).searchParams.get("conversation")).toBe(documentId));
  await waitFor(() => expect(screen.getByLabelText("你的问题")).toHaveValue(""));
  fireEvent.change(screen.getByLabelText("你的问题"), { target: { value: "第二个对话独立输入" } });
  fireEvent.click(within(history).getByRole("button", { name: /库存问答/ }));
  await waitFor(() => expect(screen.getByLabelText("你的问题")).toHaveValue("只给第一个对话的未发送内容"));
  fireEvent.click(within(history).getByRole("button", { name: /另一位客户的对话/ }));
  await waitFor(() => expect(screen.getByLabelText("你的问题")).toHaveValue("第二个对话独立输入"));
  expect(post).not.toHaveBeenCalled();
});

test("quick prompts fill an empty conversation without sending a message or creating a draft", async () => {
  mocks(conversation([])); show([...full, "purchase.order.write", "supplier.read"]);
  await waitFor(() => expect(screen.getByLabelText("你的问题")).toBeEnabled());
  for (const [label, expected] of [["查询库存", /库存/], ["了解经营情况", /经营/], ["准备销售草稿", /销售/], ["准备采购草稿", /采购/]] as const) {
    fireEvent.click(await screen.findByRole("button", { name: label }));
    expect((screen.getByLabelText("你的问题") as HTMLTextAreaElement).value).toMatch(expected);
    expect(screen.getAllByRole("textbox", { name: "你的问题" })).toHaveLength(1);
    expect(post).not.toHaveBeenCalled();
  }
});


test("temporary history read failure preserves the dirty editor and blocks approval until recovery", async () => {
  const value = conversation([{ ...finished, state: "WAITING", proposal: proposal() }]);
  mocks(value); show();
  const editor = await screen.findByRole("region", { name: "开单复核" });
  const input = within(editor).getByLabelText("第 1 行数量");
  fireEvent.change(input, { target: { value: "2.000001" } });
  get.mockImplementation(async (path) => path === "/api/v1/ai/conversations/{id}"
    ? { error: { code: "SERVICE_UNAVAILABLE", detail: "临时读取失败" }, response: new Response(null, { status: 503 }) }
    : path === "/api/v1/ai/status" ? ok(status) : ok({ items: [summary], total: 1, page: 1, page_size: 20 }));
  fireEvent.click(screen.getByLabelText("对话操作"));
  fireEvent.click(screen.getByRole("button", { name: "重新读取记录" }));
  const context = screen.getByRole("complementary", { name: "业务依据与草稿" });
  await within(context).findByText(/当前编辑已保留/);
  expect(within(editor).getByRole("button", { name: "重新预览" })).toBeDisabled();
  expect(within(editor).getByLabelText("第 1 行数量")).toBe(input);
  expect(input).toHaveValue("2.000001");
  expect(within(editor).getByRole("button", { name: "确认创建草稿" })).toBeDisabled();
  expect(editor).not.toHaveTextContent("服务器核算合计");
  mocks(value);
  fireEvent.click(within(context).getByRole("button", { name: "重新读取记录" }));
  await waitFor(() => expect(within(context).queryByText(/当前编辑已保留/)).not.toBeInTheDocument());
  expect(within(editor).getByLabelText("第 1 行数量")).toBe(input);
  expect(input).toHaveValue("2.000001");
  expect(within(editor).getByRole("button", { name: "确认创建草稿" })).toBeDisabled();
  expect(within(editor).getByRole("button", { name: "重新预览" })).toBeEnabled();
  expect(post).not.toHaveBeenCalled();
});


test("a passive history refresh cannot replace the conversation of an unknown original submission", async () => {
  const second = { ...summary, id: documentId, title: "后来插入的对话" };
  let items = [summary];
  get.mockImplementation(async (path, options) => {
    if (path === "/api/v1/ai/status") return ok(status);
    if (path === "/api/v1/ai/conversations") return ok({ items, total: items.length, page: 1, page_size: 20 });
    if (path === "/api/v1/ai/conversations/{id}") return ok(options?.params?.path?.id === documentId
      ? { ...second, turns: [], older_turns_omitted: false } : conversation());
    return ok({ items: [], total: 0 });
  });
  const { qc } = show(); await screen.findByRole("region", { name: "库存余额" });
  post.mockRejectedValueOnce(new TypeError("connection lost"));
  post.mockResolvedValueOnce(ok({ ...finished, id: "turn-2" }));
  fireEvent.change(screen.getByLabelText("你的问题"), { target: { value: "A 对话尚未确认的问题" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  await screen.findByText(/提交结果待确认/);
  const original = post.mock.calls[0];
  items = [second, summary];
  const list = qc.getQueryCache().getAll().find((query) => query.queryKey.includes("list"))!;
  act(() => { qc.setQueryData(list.queryKey, { items, total: 2, page: 1, page_size: 20 }); });
  await screen.findByRole("button", { name: second.title });
  expect(screen.getByRole("heading", { name: "库存问答" })).toBeVisible();
  expect(screen.getByLabelText("你的问题")).toHaveValue("A 对话尚未确认的问题");
  expect(screen.getByLabelText("你的问题")).toBeDisabled();
  expect(original[1]?.params?.path).toEqual({ id });
  fireEvent.click(screen.getByRole("button", { name: "重试原提交" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1]).toEqual(original);
  await waitFor(() => expect(screen.getByLabelText("你的问题")).toHaveValue(""));
  expect(screen.getByRole("heading", { name: "库存问答" })).toBeVisible();
});

test("confirmed deletion selects the surviving conversation without reloading the deleted record", async () => {
  const second = { ...summary, id: documentId, title: "保留的对话" };
  let deleted = false;
  get.mockImplementation(async (path, options) => {
    if (path === "/api/v1/ai/status") return ok(status);
    if (path === "/api/v1/ai/conversations") return ok({ items: deleted ? [second] : [summary, second], total: deleted ? 1 : 2, page: 1, page_size: 20 });
    if (path === "/api/v1/ai/conversations/{id}") return ok(options?.params?.path?.id === documentId
      ? { ...second, turns: [], older_turns_omitted: false } : conversation());
    return ok({ items: [], total: 0 });
  });
  const remove = api.DELETE as unknown as Mock;
  remove.mockImplementation(async () => { deleted = true; return ok({ deleted: true }); });
  show(); await screen.findByRole("region", { name: "库存余额" });
  fireEvent.change(screen.getByLabelText("你的问题"), { target: { value: "随已删除对话移除的输入" } });
  fireEvent.click(screen.getByLabelText("对话操作"));
  fireEvent.click(screen.getByRole("button", { name: "删除对话" }));
  get.mockClear();
  fireEvent.click(screen.getByRole("button", { name: "确认删除对话" }));
  await screen.findByRole("heading", { name: "保留的对话" });
  expect(remove.mock.calls[0][1].params.path).toEqual({ id });
  expect(remove.mock.calls[0][1].params.header["Idempotency-Key"]).toBeTruthy();
  expect(screen.getByLabelText("你的问题")).toHaveValue("");
  expect(screen.queryByRole("region", { name: "库存余额" })).not.toBeInTheDocument();
  expect(get.mock.calls.filter(([path, options]) => path === "/api/v1/ai/conversations/{id}" && options?.params?.path?.id === id)).toEqual([]);
});

test.each([true, false])("copy reply uses the selected server answer and reports clipboard availability: %s", async (available) => {
  const descriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
  const writeText = available ? vi.fn().mockResolvedValue(undefined) : vi.fn().mockRejectedValue(new Error("unavailable"));
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  try {
    mocks(); show(); await screen.findByRole("region", { name: "库存余额" });
    fireEvent.click(screen.getByRole("button", { name: "复制回复" }));
    await screen.findByText(available ? "已复制" : "无法访问剪贴板，请选中文字复制。");
    expect(writeText).toHaveBeenCalledExactlyOnceWith(finished.answer);
    expect(post).not.toHaveBeenCalled();
  } finally {
    if (descriptor) Object.defineProperty(navigator, "clipboard", descriptor);
    else Reflect.deleteProperty(navigator, "clipboard");
  }
});

test.each([404, 410])("a terminal history response clears retained facts and dirty drafts: %s", async (statusCode) => {
  mocks(conversation([{ ...finished, state: "WAITING", proposal: proposal() }])); show();
  const editor = await screen.findByRole("region", { name: "开单复核" });
  const input = within(editor).getByLabelText("第 1 行数量");
  fireEvent.change(input, { target: { value: "2.000001" } });
  get.mockImplementation(async (path) => path === "/api/v1/ai/conversations/{id}"
    ? { error: { code: statusCode === 410 ? "AI_HISTORY_EXPIRED" : "NOT_FOUND",
      detail: statusCode === 410 ? "对话正文已到期或删除，请开始新对话" : "对话不存在或无权访问" },
    response: new Response(null, { status: statusCode }) }
    : path === "/api/v1/ai/status" ? ok(status) : ok({ items: [summary], total: 1, page: 1, page_size: 20 }));
  fireEvent.click(screen.getByLabelText("对话操作"));
  fireEvent.click(screen.getByRole("button", { name: "重新读取记录" }));
  await waitFor(() => expect(screen.queryByRole("region", { name: "库存余额" })).not.toBeInTheDocument());
  expect(screen.queryByRole("region", { name: "开单复核" })).not.toBeInTheDocument();
  expect(input.isConnected).toBe(false);
  expect(screen.queryByText(finished.answer)).not.toBeInTheDocument();
  expect(screen.queryByText(finished.prompt)).not.toBeInTheDocument();
  expect(screen.getByLabelText("你的问题")).toBeDisabled();
  expect(screen.getByRole("button", { name: "新对话" })).toBeEnabled();
  expect(screen.getByRole("alert")).toHaveTextContent(/对话.*(失效|到期|删除|无法)/);
  expect(post).not.toHaveBeenCalled();
});
