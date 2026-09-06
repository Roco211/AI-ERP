import type { components } from "@/generated/api/schema";

type Turn = components["schemas"]["TurnRead"];
type StreamEvent = components["schemas"]["AssistantStreamEvent"];

export type AssistantStreamOptions = {
  signal?: AbortSignal;
  /** Server snapshots may have no answer while deltas are arriving. Preserve the
   * accumulated text until the returned promise supplies the final receipt. */
  onTurn?: (turn: Turn) => void;
  /** Append these actual upstream text fragments; never infer token activity. */
  onDelta?: (delta: string, turnId: string) => void;
};

export class AssistantStreamFailure extends Error {
  constructor(
    message: string,
    public code = "AI_STREAM_INTERRUPTED",
    public status = 503,
    public requestId?: string,
  ) { super(message); }
}

const MAX_FRAME = 2 * 1024 * 1024;
const MAX_RESPONSE = 32 * 1024 * 1024;
const MAX_DELTA_TEXT = 100_000;
const interrupted = () => new AssistantStreamFailure("连接中断，请重试原提交以确认结果。");
const invalid = () => new AssistantStreamFailure("接收到的流式数据格式不完整，请重试原提交以确认结果。", "AI_STREAM_INVALID");
const tooLarge = () => new AssistantStreamFailure("流式响应超出大小限制，请重试原提交以确认结果。", "AI_STREAM_LIMIT");
const aborted = () => new AssistantStreamFailure("已停止接收回复，请重试原提交以确认结果。", "AI_STREAM_ABORTED");
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const nullableString = (value: unknown) => value == null || typeof value === "string";

export function readAssistantTurn(value: unknown): Turn {
  if (!object(value) || typeof value.id !== "string" || !value.id ||
    !["RUNNING", "WAITING", "COMPLETED", "FAILED"].includes(String(value.state)) ||
    typeof value.prompt !== "string" || typeof value.created_at !== "string" ||
    !Number.isSafeInteger(value.model_calls) || !Number.isSafeInteger(value.tool_calls) ||
    typeof value.can_retry !== "boolean" || !nullableString(value.answer) ||
    !nullableString(value.error_code) || !nullableString(value.error_message) ||
    (value.evidence !== undefined && !Array.isArray(value.evidence)) ||
    (value.proposal != null && !object(value.proposal)) ||
    (value.interaction !== undefined && !["business", "casual"].includes(String(value.interaction))) ||
    (value.guided !== undefined && typeof value.guided !== "boolean")) throw invalid();
  if (value.activity !== undefined && (!Array.isArray(value.activity) || value.activity.length > 32 ||
    value.activity.some((entry) => !object(entry) || typeof entry.id !== "string" ||
      typeof entry.title !== "string" || typeof entry.started_at !== "string" ||
      !nullableString(entry.finished_at) || !["model", "query", "draft", "reply"].includes(String(entry.kind)) ||
      !["running", "complete", "failed"].includes(String(entry.state))))) throw invalid();
  return value as Turn;
}

function terminalTurn(value: unknown): Turn {
  const turn = readAssistantTurn(value);
  // A repeated request can acknowledge a still-running lease. It is not a
  // receipt; retaining its original key is necessary for safe recovery.
  if (turn.state === "RUNNING") throw interrupted();
  return turn;
}

/** A request owns its decoder, callbacks and buffers; none survive identity changes. */
export async function readAssistantStream(response: Response, options: AssistantStreamOptions): Promise<Turn> {
  const contentType = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  const jsonResponse = contentType === "application/json";
  if (!jsonResponse && contentType !== "text/event-stream") throw invalid();
  if (!response.body) throw interrupted();
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let responseBytes = 0;
  let buffer = "";
  let frameBytes = 0;
  let data: string[] = [];
  let turnId: string | undefined;
  let deltaLength = 0;
  let final: Turn | undefined;
  let reachedEnd = false;
  const onAbort = () => { void reader.cancel().catch(() => undefined); };
  options.signal?.addEventListener("abort", onAbort, { once: true });

  function decode(chunk?: Uint8Array, streaming = false) {
    try { return decoder.decode(chunk, { stream: streaming }); } catch { throw invalid(); }
  }

  function receiveFrame() {
    if (!data.length) return;
    let parsed: unknown;
    try { parsed = JSON.parse(data.join("\n")); } catch { throw invalid(); }
    if (!object(parsed) || !["snapshot", "delta", "complete", "error"].includes(String(parsed.type))) throw invalid();
    const event = parsed as StreamEvent;
    if (event.type === "error") {
      if (!Number.isInteger(event.status) || event.status! < 400 || event.status! > 599 ||
        typeof event.code !== "string" || typeof event.detail !== "string" ||
        !nullableString(event.request_id)) throw invalid();
      throw new AssistantStreamFailure(event.detail, event.code, event.status!, event.request_id ?? undefined);
    }
    if (event.type === "delta") {
      if (typeof event.turn_id !== "string" || !event.turn_id ||
        typeof event.delta !== "string" || event.delta.length > 4000) throw invalid();
      if (!turnId || event.turn_id !== turnId) throw invalid();
      deltaLength += event.delta.length;
      if (deltaLength > MAX_DELTA_TEXT) throw tooLarge();
      options.onDelta?.(event.delta, event.turn_id);
      return;
    }
    const turn = event.type === "complete" ? terminalTurn(event.turn) : readAssistantTurn(event.turn);
    if (turnId && turn.id !== turnId) throw invalid();
    turnId = turn.id;
    if (event.type === "complete") final = turn;
    options.onTurn?.(turn);
  }

  function receiveLine(line: string) {
    frameBytes += line.length;
    if (frameBytes > MAX_FRAME) throw tooLarge();
    if (line === "") {
      receiveFrame();
      data = [];
      frameBytes = 0;
    } else if (line.startsWith("data:")) {
      data.push(line.slice(5).replace(/^ /, ""));
    } else if (line === "data") {
      data.push("");
    }
    // SSE comments, event, id and retry metadata carry no application activity.
  }

  function consumeLines(end = false) {
    let start = 0;
    for (let index = 0; index < buffer.length; index++) {
      const char = buffer[index];
      if (char !== "\r" && char !== "\n") continue;
      if (char === "\r" && index === buffer.length - 1 && !end) break;
      receiveLine(buffer.slice(start, index));
      if (char === "\r" && buffer[index + 1] === "\n") index++;
      start = index + 1;
      if (final) break;
    }
    buffer = buffer.slice(start);
    if (buffer.length + frameBytes > MAX_FRAME) throw tooLarge();
  }

  try {
    if (options.signal?.aborted) throw aborted();
    while (true) {
      const chunk = await reader.read();
      if (options.signal?.aborted) throw aborted();
      if (chunk.done) { reachedEnd = true; break; }
      responseBytes += chunk.value.byteLength;
      if (responseBytes > MAX_RESPONSE) throw tooLarge();
      buffer += decode(chunk.value, true);
      if (jsonResponse) continue;
      consumeLines();
      if (final) return final;
    }
    buffer += decode();
    if (jsonResponse) {
      let parsed: unknown;
      try { parsed = JSON.parse(buffer); } catch { throw invalid(); }
      const turn = terminalTurn(parsed);
      options.onTurn?.(turn);
      return turn;
    }
    consumeLines(true);
    if (final) return final;
    // SSE requires a blank line to dispatch a frame. Never promote a dangling
    // partial frame or snapshot to the final, committed response.
    throw interrupted();
  } catch (error) {
    if (error instanceof AssistantStreamFailure) throw error;
    if (options.signal?.aborted) throw aborted();
    throw interrupted();
  } finally {
    options.signal?.removeEventListener("abort", onAbort);
    if (!reachedEnd) void reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
