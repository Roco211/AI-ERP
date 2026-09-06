import { afterEach, expect, test, vi } from "vitest";
import { AssistantError, requestBrief, retryTurn, sendMessage, type Turn } from "@/features/ai/assistant-client";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { POST: vi.fn() },
  ApiError: class extends Error {
    constructor(public status: number, message: string, public requestId?: string) { super(message); }
  },
}));

const encoder = new TextEncoder();
const turn = (patch: Partial<Turn> = {}): Turn => ({
  id: "turn-1", prompt: "你好", state: "COMPLETED", created_at: "2026-09-06T12:00:00Z",
  answer: "你好，可以一起看看库存。", evidence: [], proposal: null,
  model_calls: 1, tool_calls: 0, can_retry: false, interaction: "business", guided: false, ...patch,
});
const frame = (event: unknown, newline = "\n") => `data: ${JSON.stringify(event)}${newline}${newline}`;
const complete = (value = turn()) => frame({ type: "complete", turn: value });
function response(chunks: (string | Uint8Array)[], end = true) {
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
      if (end) controller.close();
    },
  }), { headers: { "Content-Type": "text/event-stream; charset=utf-8" } });
}
function transport(result: Response | Promise<Response>) {
  const fetchMock = vi.fn().mockResolvedValue(result);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.resetAllMocks(); });

test("the existing JSON callers retain their original API method, body and key", async () => {
  vi.mocked(api.POST).mockResolvedValue({ data: turn(), response: new Response() } as never);
  const body = { message: "你好", selected_ids: [] };
  expect(await sendMessage("conversation-1", body, "original-key")).toEqual(turn());
  expect(api.POST).toHaveBeenCalledWith("/api/v1/ai/conversations/{id}/messages", {
    params: { path: { id: "conversation-1" }, header: { "Idempotency-Key": "original-key" } }, body,
  });
});

test("JSON callers reject an SSE envelope instead of accepting it as a turn", async () => {
  vi.mocked(api.POST).mockResolvedValue({ data: { type: "complete", turn: turn() }, response: new Response() } as never);
  await expect(sendMessage("conversation-1", { message: "你好" }, "original-key")).rejects.toMatchObject({
    status: 503, code: "AI_RESPONSE_INVALID", submissionUncertain: true,
  });
});

test("SSE parses split UTF8 and CRLF, comments and multiple frames while preserving exact text", async () => {
  const stream = ": keep-alive\r\n\r\n" +
    frame({ type: "snapshot", turn: turn({ state: "RUNNING", answer: null }) }, "\r\n") +
    frame({ type: "delta", turn_id: "turn-1", delta: "你好，😀" }, "\r\n") +
    frame({ type: "delta", turn_id: "turn-1", delta: "一起看看库存。" }, "\r\n") + complete();
  const bytes = encoder.encode(stream);
  // Byte-sized network chunks split Chinese, supplementary Unicode and CRLF.
  const fetchMock = transport(response(Array.from(bytes, (byte) => Uint8Array.of(byte))));
  const onTurn = vi.fn();
  const fragments: string[] = [];
  const signal = new AbortController().signal;
  const result = await sendMessage("conversation-1", { message: "你好", selected_ids: [] }, "stable-key", {
    signal, onTurn, onDelta: (delta, id) => { expect(id).toBe("turn-1"); fragments.push(delta); },
  });
  expect(fragments.join("")).toBe("你好，😀一起看看库存。");
  expect(result).toEqual(turn());
  expect(onTurn.mock.calls.map(([value]) => value.state)).toEqual(["RUNNING", "COMPLETED"]);
  const [url, options] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/v1/ai/conversations/conversation-1/messages");
  expect(options).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
    headers: { Accept: "text/event-stream", "Content-Type": "application/json", "Idempotency-Key": "stable-key" }, signal });
  expect(options.body).toBe(JSON.stringify({ message: "你好", selected_ids: [] }));
  expect(api.POST).not.toHaveBeenCalled();
});

test("snapshots and deltas arrive before the final response, with no simulated progress", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  transport(new Response(new ReadableStream<Uint8Array>({ start(value) { controller = value; } }),
    { headers: { "Content-Type": "text/event-stream" } }));
  const onTurn = vi.fn();
  const onDelta = vi.fn();
  let settled = false;
  const pending = sendMessage("conversation-1", { message: "你好" }, "key", { onTurn, onDelta })
    .finally(() => { settled = true; });
  controller.enqueue(encoder.encode(": keep-alive\n\n"));
  await Promise.resolve();
  expect(onTurn).not.toHaveBeenCalled();
  expect(onDelta).not.toHaveBeenCalled();
  controller.enqueue(encoder.encode(frame({ type: "snapshot", turn: turn({ state: "RUNNING", answer: null }) }) +
    frame({ type: "delta", turn_id: "turn-1", delta: "你好" })));
  await vi.waitFor(() => expect(onDelta).toHaveBeenCalledWith("你好", "turn-1"));
  expect(settled).toBe(false);
  controller.enqueue(encoder.encode(complete()));
  await expect(pending).resolves.toEqual(turn());
});

test("multiline data fields and a bare CR separator follow SSE framing", async () => {
  transport(response([`event: message\rid: 8\rdata: {"type":"complete",\rdata: "turn":${JSON.stringify(turn())}}\r\r`]));
  await expect(requestBrief("conversation-1", undefined, "key", {})).resolves.toEqual(turn());
});

test.each(["WAITING", "FAILED"] as const)("a terminal %s business turn resolves as the final receipt", async (state) => {
  const value = turn({ state, error_code: state === "FAILED" ? "AI_PROVIDER_UNAVAILABLE" : null,
    error_message: state === "FAILED" ? "模型暂时不可用" : null, can_retry: state === "FAILED" });
  transport(response([complete(value)]));
  await expect(retryTurn("turn-1", "key", {})).resolves.toEqual(value);
});

test("a complete event holding a still-running lease keeps the submission uncertain", async () => {
  transport(response([complete(turn({ state: "RUNNING", answer: null }))]));
  const onTurn = vi.fn();
  await expect(retryTurn("turn-1", "key", { onTurn })).rejects.toMatchObject({
    status: 503, code: "AI_STREAM_INTERRUPTED", submissionUncertain: true,
  });
  expect(onTurn).not.toHaveBeenCalled();
});

test.each([
  ["partial snapshot", frame({ type: "snapshot", turn: turn({ state: "RUNNING", answer: null }) })],
  ["dangling final frame", complete().trimEnd()],
  ["only a heartbeat", ": keep-alive\n\n"],
])("%s cannot become a successful final response", async (_name, value) => {
  transport(response([value]));
  await expect(sendMessage("conversation-1", { message: "你好" }, "key", {})).rejects.toMatchObject({
    status: 503, code: "AI_STREAM_INTERRUPTED", submissionUncertain: true,
  });
});

test("a network failure after a partial delta preserves the same key and body for recovery", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const fetchMock = transport(new Response(new ReadableStream<Uint8Array>({ start(value) { controller = value; } }),
    { headers: { "Content-Type": "text/event-stream" } }));
  const onDelta = vi.fn();
  const body = { message: "你好", selected_ids: [] };
  const pending = sendMessage("conversation-1", body, "original-key", { onDelta });
  const assertion = expect(pending).rejects.toMatchObject({ status: 503, submissionUncertain: true });
  controller.enqueue(encoder.encode(frame({ type: "snapshot", turn: turn({ state: "RUNNING", answer: null }) }) +
    frame({ type: "delta", turn_id: "turn-1", delta: "你好" })));
  await vi.waitFor(() => expect(onDelta).toHaveBeenCalled());
  controller.error(new TypeError("Network disconnected"));
  await assertion;
  fetchMock.mockResolvedValueOnce(response([complete()]));
  await sendMessage("conversation-1", body, "original-key", { onDelta });
  expect(fetchMock.mock.calls[1]).toEqual(fetchMock.mock.calls[0]);
});

test.each([401, 403, 409, 503])("an in-stream %s error preserves server authority information and uncertainty", async (status) => {
  transport(response([frame({ type: "error", status, code: "AI_CONTEXT_CHANGED", detail: "会话上下文已变化", request_id: "request-1" })]));
  await expect(retryTurn("turn-1", "key", {})).rejects.toMatchObject({
    status, code: "AI_CONTEXT_CHANGED", message: "会话上下文已变化", requestId: "request-1", submissionUncertain: true,
  });
});

test("HTTP 409 ProblemDetails remains a definite rejection, with no provider credential header", async () => {
  const fetchMock = transport(new Response(JSON.stringify({ detail: "请求键已用于其他内容", code: "IDEMPOTENCY_CONFLICT",
    request_id: "request-409" }), { status: 409, headers: { "Content-Type": "application/problem+json" } }));
  await expect(sendMessage("conversation-1", { message: "库存" }, "user-key", {})).rejects.toMatchObject({
    status: 409, code: "IDEMPOTENCY_CONFLICT", requestId: "request-409", submissionUncertain: false,
  });
  expect(Object.keys(fetchMock.mock.calls[0][1].headers).sort()).toEqual(["Accept", "Content-Type", "Idempotency-Key"]);
});

test.each([
  ["malformed JSON", "data: {broken}\n\n"],
  ["unknown event", frame({ type: "thought", content: "hidden content" })],
  ["invalid turn", frame({ type: "complete", turn: { id: "turn-1", state: "COMPLETED" } })],
  ["delta before snapshot", frame({ type: "delta", turn_id: "turn-1", delta: "hi" })],
  ["different turn ID", frame({ type: "snapshot", turn: turn({ state: "RUNNING" }) }) + complete(turn({ id: "turn-2" }))],
])("%s is rejected without JSON fallback or exposing payload contents", async (_name, value) => {
  transport(response([value]));
  try { await retryTurn("turn-1", "key", {}); throw new Error("Expected stream rejection"); }
  catch (error) {
    expect(error).toBeInstanceOf(AssistantError);
    expect(error).toMatchObject({ status: 503, code: "AI_STREAM_INVALID", submissionUncertain: true });
    expect((error as Error).message).not.toContain("hidden content");
  }
});

test("oversized frames fail within a bounded buffer", async () => {
  transport(response(["data: " + "x".repeat(2 * 1024 * 1024 + 1)]));
  await expect(retryTurn("turn-1", "key", {})).rejects.toMatchObject({ code: "AI_STREAM_LIMIT", submissionUncertain: true });
});

test("invalid UTF8 is not silently replaced", async () => {
  transport(response([Uint8Array.of(0xc3, 0x28)]));
  await expect(retryTurn("turn-1", "key", {})).rejects.toMatchObject({ code: "AI_STREAM_INVALID", submissionUncertain: true });
});

test("the declared JSON fallback accepts only a complete validated turn", async () => {
  transport(new Response(JSON.stringify(turn()), { headers: { "Content-Type": "application/json" } }));
  const onTurn = vi.fn();
  const onDelta = vi.fn();
  await expect(requestBrief("conversation-1", "2026-09-06", "key", { onTurn, onDelta })).resolves.toEqual(turn());
  expect(onTurn).toHaveBeenCalledExactlyOnceWith(turn());
  expect(onDelta).not.toHaveBeenCalled();
});

test("aborting a waiting read cancels its reader and retains uncertainty", async () => {
  const canceled = vi.fn();
  transport(new Response(new ReadableStream<Uint8Array>({ cancel: canceled }), { headers: { "Content-Type": "text/event-stream" } }));
  const aborter = new AbortController();
  const pending = retryTurn("turn-1", "key", { signal: aborter.signal });
  const assertion = expect(pending).rejects.toMatchObject({ code: "AI_STREAM_ABORTED", submissionUncertain: true });
  await Promise.resolve();
  aborter.abort();
  await assertion;
  expect(canceled).toHaveBeenCalledTimes(1);
});

test("simultaneous identity-scoped requests never share keys, turn IDs or callbacks", async () => {
  const one = turn({ id: "owner-1-turn" });
  const two = turn({ id: "owner-2-turn" });
  const fetchMock = vi.fn().mockResolvedValueOnce(response([complete(one)]))
    .mockResolvedValueOnce(response([complete(two)]));
  vi.stubGlobal("fetch", fetchMock);
  const first = vi.fn();
  const second = vi.fn();
  await Promise.all([
    sendMessage("owner-1-conversation", { message: "一" }, "owner-1-key", { onTurn: first }),
    sendMessage("owner-2-conversation", { message: "二" }, "owner-2-key", { onTurn: second }),
  ]);
  expect(first).toHaveBeenCalledExactlyOnceWith(one);
  expect(second).toHaveBeenCalledExactlyOnceWith(two);
  expect(fetchMock.mock.calls.map(([, options]) => options.headers["Idempotency-Key"]))
    .toEqual(["owner-1-key", "owner-2-key"]);
});
