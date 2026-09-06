import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, test, vi } from "vitest";
import { AssistantWorkspace } from "@/features/ai/workspace";
import { api } from "@/lib/api";
import type { Turn } from "@/features/ai/assistant-client";

vi.mock("@/lib/api", () => ({ api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn() },
  ApiError: class extends Error { constructor(public status: number, message: string) { super(message); } },
}));
const id = "10000000-0000-4000-8000-000000000001";
const stamp = "2026-09-06T10:00:00Z";
const ongoing: Turn = { id: "turn-stream", prompt: "今天想聊聊", answer: null, state: "RUNNING",
  created_at: stamp, evidence: [], proposal: null, can_retry: false, model_calls: 1, tool_calls: 0,
  guided: false, interaction: "casual", error_code: null, error_message: null,
  activity: [{ id: "route", kind: "model", title: "理解你的请求", state: "complete", started_at: stamp, finished_at: stamp },
    { id: "reply", kind: "reply", title: "组织回复", state: "running", started_at: stamp, finished_at: null }] };
const completed: Turn = { ...ongoing, state: "COMPLETED", answer: "你好，我们可以聊聊。",
  activity: ongoing.activity!.map((item) => ({ ...item, state: "complete", finished_at: stamp })) };
const encoder = new TextEncoder();

function harness(initial: Turn[] = []) {
  let history = initial;
  let writer: ReadableStreamDefaultController<Uint8Array>;
  let cancelled = false;
  const body = new ReadableStream<Uint8Array>({ start(controller) { writer = controller; }, cancel() { cancelled = true; } });
  const fetcher = vi.fn().mockResolvedValue(new Response(body, { headers: { "Content-Type": "text/event-stream" } }));
  vi.stubGlobal("fetch", fetcher);
  vi.mocked(api.GET).mockImplementation(async (path) => ({ response: new Response(), data:
    path === "/api/v1/ai/status" ? { configured: true, can_manage_provider: false, provider_name: "测试模型", model: "controlled" }
      : path === "/api/v1/ai/conversations" ? { items: [{ id, title: "流式对话", created_at: stamp }], total: 1, page: 1, page_size: 20 }
        : { id, title: "流式对话", created_at: stamp, expires_at: "2099-09-13T10:00:00Z", turns: history, older_turns_omitted: false },
  }) as never);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = (identity: string, permissions = ["ai.use"]) => <QueryClientProvider client={qc}>
    <AssistantWorkspace permissions={permissions} identityKey={identity} /></QueryClientProvider>;
  const view = render(tree("owner-1"));
  return { fetcher, qc, cancelled: () => cancelled,
    identity: (value: string, permissions?: string[]) => view.rerender(tree(value, permissions)),
    history: (value: Turn[]) => { history = value; },
    frame: async (value: unknown) => { await act(async () => { writer.enqueue(encoder.encode(`data: ${JSON.stringify(value)}\n\n`)); }); },
    fail: async () => { await act(async () => { writer.error(new TypeError("Connection lost")); }); },
  };
}
async function send() {
  const input = await screen.findByRole("textbox", { name: "你的问题" });
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, { target: { value: "今天想聊聊" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
}
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

test("public activity and real deltas appear before completion, snapshots preserve partial text, and final receipt unlocks", async () => {
  const h = harness(); await send();
  await h.frame({ type: "snapshot", turn: ongoing });
  expect(await screen.findByText("理解你的请求")).toBeVisible();
  const announcement = screen.getByRole("status", { name: "助手处理状态" });
  const transcript = screen.getByRole("log");
  expect(announcement).toHaveTextContent("组织回复");
  expect(announcement).toHaveAttribute("aria-atomic", "true");
  expect(transcript).toHaveAttribute("aria-busy", "true");
  expect(transcript).not.toContainElement(announcement);
  await h.frame({ type: "delta", turn_id: ongoing.id, delta: "你好，" });
  expect(await screen.findByText("你好，")).toBeVisible();
  expect(announcement).toHaveTextContent("组织回复");
  expect(announcement).not.toHaveTextContent("你好");
  expect(screen.getByRole("textbox", { name: "你的问题" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "复制回复" })).toBeNull();
  // Snapshot contains persisted activity, not accumulated reply text.
  await h.frame({ type: "snapshot", turn: { ...ongoing, tool_calls: 0 } });
  expect(screen.getByText("你好，")).toBeVisible();
  await h.frame({ type: "delta", turn_id: ongoing.id, delta: "我们可以聊聊。" });
  expect(screen.getByText(completed.answer!)).toBeVisible();
  h.history([completed]);
  await h.frame({ type: "complete", turn: completed });
  await waitFor(() => expect(screen.getByRole("textbox", { name: "你的问题" })).toBeEnabled());
  expect(announcement).toBeEmptyDOMElement();
  expect(transcript).toHaveAttribute("aria-busy", "false");
  expect(screen.getByRole("textbox", { name: "你的问题" })).toHaveValue("");
  expect(screen.getByRole("button", { name: "已完成 2 项处理" })).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByRole("button", { name: "复制回复" })).toBeEnabled();
  const [, init] = h.fetcher.mock.calls[0];
  expect(new Headers(init.headers).get("accept")).toBe("text/event-stream");
  expect(init.credentials).toBe("same-origin");
});

test("interrupted partial output is discarded and original body/key recovers without duplicate execution", async () => {
  const h = harness(); await send();
  await h.frame({ type: "snapshot", turn: ongoing });
  await h.frame({ type: "delta", turn_id: ongoing.id, delta: "尚未确认的片段" });
  expect(screen.getByText("尚未确认的片段")).toBeVisible();
  await h.fail();
  await screen.findByText(/提交结果待确认/);
  expect(screen.queryByText("尚未确认的片段")).toBeNull();
  expect(screen.getByRole("button", { name: "新对话" })).toBeDisabled();
  const [url, original] = h.fetcher.mock.calls[0];
  h.history([completed]);
  h.fetcher.mockResolvedValueOnce(Response.json(completed));
  fireEvent.click(screen.getByRole("button", { name: "重试原提交" }));
  await waitFor(() => expect(h.fetcher).toHaveBeenCalledTimes(2));
  const [retryUrl, retried] = h.fetcher.mock.calls[1];
  expect(retryUrl).toBe(url);
  expect(retried.body).toBe(original.body);
  expect(new Headers(retried.headers).get("Idempotency-Key")).toBe(new Headers(original.headers).get("Idempotency-Key"));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "你的问题" })).toBeEnabled());
  expect(screen.getAllByText(completed.answer!)).toHaveLength(1);
});

test("validated query data appears as an exact result card before the final business reply", async () => {
  const h = harness(); await send();
  const inventory: Turn = { ...ongoing, interaction: "business", evidence: [{ id: "e1", tool: "get_inventory",
    title: "实时库存", as_of: stamp, scope: "本轮服务器查询", summary: [], columns: ["商品", "可用数量"],
    rows: [{ 商品: "M8 螺栓", 可用数量: "17.000002" }], links: [], truncated: false }] };
  await h.frame({ type: "snapshot", turn: inventory });
  const summary = await screen.findByRole("region", { name: "实时库存结果摘要" });
  expect(summary).toHaveTextContent("17.000002");
  expect(screen.getByRole("textbox", { name: "你的问题" })).toBeDisabled();
  fireEvent.click(within(summary).getByRole("button", { name: "查看业务依据" }));
  expect(screen.getByRole("region", { name: "实时库存" })).toHaveTextContent("本轮服务器查询");
  expect(h.fetcher).toHaveBeenCalledTimes(1);
});

test("a midstream permission rejection clears private text and preserves the uncertain submission key", async () => {
  const h = harness(); await send();
  await h.frame({ type: "snapshot", turn: ongoing });
  await h.frame({ type: "delta", turn_id: ongoing.id, delta: "本身份的临时回复" });
  await h.frame({ type: "error", status: 403, code: "FORBIDDEN", detail: "权限已变化" });
  await screen.findByText(/旧对话已停止展示/);
  expect(screen.queryByText("本身份的临时回复")).toBeNull();
  expect(screen.getByRole("button", { name: "重试原提交" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "新对话" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "你的问题" })).toBeDisabled();
});

test("revoking access unmounts and aborts the stream with no text or callback leaking to another identity", async () => {
  const h = harness(); await send();
  await h.frame({ type: "snapshot", turn: ongoing });
  await h.frame({ type: "delta", turn_id: ongoing.id, delta: "仅旧身份可见" });
  h.identity("owner-2", []);
  expect(screen.queryByText("仅旧身份可见")).toBeNull();
  await waitFor(() => expect(h.fetcher.mock.calls[0][1].signal.aborted).toBe(true));
  expect(h.cancelled()).toBe(true);
});

test("a FAILED final receipt replaces provisional text, exposes real failed activity and enables server turn recovery", async () => {
  const h = harness(); await send();
  await h.frame({ type: "snapshot", turn: ongoing });
  await h.frame({ type: "delta", turn_id: ongoing.id, delta: "这段回复尚未完成" });
  const failed: Turn = { ...ongoing, state: "FAILED", answer: null, can_retry: true, error_message: "模型连接中断，请恢复本次处理。",
    activity: ongoing.activity!.map((item) => ({ ...item, state: "failed", finished_at: stamp })) };
  h.history([failed]);
  await h.frame({ type: "complete", turn: failed });
  await screen.findByRole("button", { name: "恢复本次处理" });
  expect(screen.queryByText("这段回复尚未完成")).toBeNull();
  expect(within(screen.getByRole("article", { name: "经营助手回复" })).getByRole("alert")).toHaveTextContent("模型连接中断");
  expect(screen.queryByRole("button", { name: "重试原提交" })).toBeNull();
});
