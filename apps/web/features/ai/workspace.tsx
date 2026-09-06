"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CalendarDays, CheckCheck, ChevronDown, ClipboardList, Copy, Database, History, MoreHorizontal, PackageSearch, PanelRightClose, PanelRightOpen, RefreshCw, ShoppingBag, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ChatApp } from "@/components/agents/chat-app";
import { Message, MessageAvatar, MessageBubble, MessageBubbleContent, MessageContent,
  MessageGroup, MessageHeader } from "@/components/agents/message";
import { MessageScroller } from "@/components/agents/message-scroller";
import { PromptInput } from "@/components/agents/prompt-input";
import { useOperationSubmission } from "@/features/operations/use-submission";
import { SubmissionFeedback } from "@/features/operations/presentation";
import { ConversationHistory } from "./conversation-history";
import { EvidenceCard, ProposalCard } from "./context-panel";
import { BusinessResultCards } from "./business-result-cards";
import { TurnActivity } from "./turn-activity";
import { ThinkingShimmer } from "@/components/agents/loading-states/thinking-shimmer";
import {
  AssistantError, getAssistantStatus, listConversations, getConversation, createConversation,
  deleteConversation, sendMessage, requestBrief, retryTurn,
  type AssistantStreamOptions, type Conversation, type ConversationsPage, type Turn,
} from "./assistant-client";

const authorityError = (error: unknown) => error instanceof AssistantError && (
  [401, 403].includes(error.status) || error.status === 409 &&
  /AUTHORITY|PERMISSION|PROVIDER|CONTEXT/.test(error.code ?? "")
);
const when = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });
type Panel = "chat" | "history" | "context";
const starters = [
  { title: "查询库存", description: "核对商品与可用数量", text: "请帮我查询以下商品的可用库存：", icon: PackageSearch, permission: "inventory.read" },
  { title: "了解经营情况", description: "从业务记录了解经营状况", text: "请根据业务记录汇总最近的经营情况。", icon: Database, permission: "dashboard.read" },
  { title: "准备销售草稿", description: "说明客户、商品和数量", text: "请准备销售草稿，客户：，商品：，数量：。先给我预览。", icon: ShoppingBag, permission: "sales.order.write" },
  { title: "准备采购草稿", description: "说明供应商与采购需求", text: "请准备采购草稿，供应商：，商品：，数量：，单价：。先给我预览。", icon: ClipboardList, permission: "purchase.order.write" },
];

export function AssistantWorkspace({ permissions, identityKey = "current" }: {
  permissions: string[]; identityKey?: string;
}) {
  if (!permissions.includes("ai.use")) return <p role="alert" className="p-6 text-sm">
    当前账户没有使用 AI 助手的权限，请联系管理员。
  </p>;
  return <AuthorizedAssistant key={identityKey + permissions.slice().sort().join("|")}
    permissions={permissions} />;
}

function AuthorizedAssistant({ permissions }: { permissions: string[] }) {
  const qc = useQueryClient();
  const [cacheScope] = useState(() => crypto.randomUUID());
  const [page, setPage] = useState(1);
  const [chosen, setChosen] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    const value = new URL(window.location.href).searchParams.get("conversation");
    return value && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value) ? value : null;
  });
  // Unsent text is private, in-memory and scoped to this identity and conversation.
  const [questions, setQuestions] = useState<Record<string, string>>({});
  const [day, setDay] = useState("");
  const [blocked, setBlocked] = useState(false);
  const [accessError, setAccessError] = useState<AssistantError | null>(null);
  const [deleteCheck, setDeleteCheck] = useState(false);
  const [panel, setPanel] = useState<Panel>("chat");
  const [contextOpen, setContextOpen] = useState(false);
  const [live, setLive] = useState<{ conversationId: string; turn: Turn; text: string } | null>(null);
  const streamController = useRef<AbortController | null>(null);
  const [contextTurn, setContextTurn] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<{ id: string; ok: boolean } | null>(null);
  const submission = useOperationSubmission({ success: "" });
  const base = ["assistant", cacheScope];
  const status = useQuery({ queryKey: [...base, "status"], queryFn: ({ signal }) => getAssistantStatus(signal),
    retry: false, gcTime: 0 });
  const conversations = useQuery({ queryKey: [...base, "list", page],
    queryFn: ({ signal }) => listConversations(page, signal), retry: false, gcTime: 0 });
  const firstConversation = conversations.data?.items[0]?.id;
  // Pin the initial selection: passive list refreshes cannot move an in-flight turn.
  if (chosen === null && firstConversation) setChosen(firstConversation);
  const id = chosen ?? firstConversation;
  const question = questions[id ?? "empty"] ?? "";
  function setQuestion(value: string) { setQuestions((old) => ({ ...old, [id ?? "empty"]: value })); }
  const detailKey = [...base, "conversation", id];
  const detail = useQuery({ queryKey: detailKey,
    queryFn: ({ signal }) => getConversation(id!, signal), enabled: !!id && !blocked,
    retry: false, gcTime: 0,
    refetchInterval: (query) => !submission.busy && query.state.data?.turns.some((turn) => turn.state === "RUNNING") ? 2500 : false,
  });
  const historyUnavailable = detail.error instanceof AssistantError && [404, 410].includes(detail.error.status);
  const rejectedRead = detail.error instanceof AssistantError && detail.error.status >= 400 && detail.error.status < 500;
  const unavailable = blocked || rejectedRead || authorityError(status.error);
  const conversation = unavailable ? undefined : detail.data;
  const visibleLive = !unavailable && live?.conversationId === id ? live : null;
  const liveTurn = visibleLive ? { ...visibleLive.turn, answer: visibleLive.text || visibleLive.turn.answer } : null;
  const turns = conversation ? liveTurn
    ? conversation.turns.some((turn) => turn.id === liveTurn.id)
      ? conversation.turns.map((turn) => turn.id === liveTurn.id ? liveTurn : turn)
      : [...conversation.turns, liveTurn]
    : conversation.turns : [];
  const readFailed = !!detail.error || !!status.error;
  const running = turns.some((turn) => turn.state === "RUNNING");
  const activeStage = turns.findLast((turn) => turn.state === "RUNNING")?.activity
    ?.findLast((item) => item.state === "running")?.title;
  const activityAnnouncement = unavailable ? "" : submission.uncertain ? "连接已中断，处理结果待确认。"
    : running || submission.busy ? activeStage ?? "正在处理请求…" : "";
  const locked = submission.locked || running || readFailed;
  const contextTurns = turns.filter((turn) => (turn.evidence ?? []).length || turn.proposal);
  const focused = contextTurns.find((turn) => turn.id === contextTurn) ?? contextTurns.at(-1);
  const pendingCount = turns.filter((turn) => turn.proposal?.status === "PENDING").length;

  useEffect(() => () => { streamController.current?.abort(); }, []);
  useEffect(() => { if (unavailable) streamController.current?.abort(); }, [unavailable]);

  useEffect(() => {
    const media = window.matchMedia("(min-width: 1280px)");
    const preserveFocus = (event: MediaQueryListEvent) => {
      if (event.matches) return;
      const active = document.activeElement?.closest<HTMLElement>("[data-assistant-panel]")?.dataset.assistantPanel;
      if (active === "chat" || active === "history" || active === "context") setPanel(active);
    };
    media.addEventListener("change", preserveFocus);
    return () => media.removeEventListener("change", preserveFocus);
  }, []);

  function showPanel(value: Panel) {
    setPanel(value);
    if (value === "context") setContextOpen(true);
    requestAnimationFrame(() => document.getElementById(`assistant-${value}-heading`)?.focus({ preventScroll: true }));
  }
  function selectConversation(value: string | null) {
    setChosen(value); setContextTurn(null); setCopyStatus(null); setLive(null);
    const url = new URL(window.location.href);
    if (value) url.searchParams.set("conversation", value);
    else url.searchParams.delete("conversation");
    window.history.replaceState(window.history.state, "", url);
  }
  async function protect<T>(operation: () => Promise<T>): Promise<T> {
    try { return await operation(); }
    catch (error) {
      if (authorityError(error)) { setBlocked(true); setAccessError(error as AssistantError); }
      else if (id) void detail.refetch();
      throw error;
    }
  }
  function acceptTurn(turn: Turn) {
    qc.setQueryData<Conversation>(detailKey, (old) => old ? {
      ...old, turns: old.turns.some((t) => t.id === turn.id)
        ? old.turns.map((t) => t.id === turn.id ? turn : t) : [...old.turns, turn],
    } : old);
    setLive(null);
    setContextTurn(turn.id);
    void detail.refetch();
  }
  async function streamed(conversationId: string, operation: (options: AssistantStreamOptions) => Promise<Turn>) {
    const controller = new AbortController();
    streamController.current = controller;
    setLive(null);
    try {
      return await operation({ signal: controller.signal,
        onTurn: (turn) => {
          if (controller.signal.aborted || streamController.current !== controller) return;
          setLive((old) => ({ conversationId, turn,
            text: turn.state !== "RUNNING" ? turn.answer ?? ""
              : old?.turn.id === turn.id ? old.text : "" }));
        },
        onDelta: (delta, turnId) => {
          if (controller.signal.aborted || streamController.current !== controller) return;
          setLive((old) => old?.turn.id === turnId ? { ...old, text: old.text + delta } : old);
        },
      });
    } catch (error) {
      // Partial output is provisional; receipt recovery owns the next visible result.
      setLive(null);
      throw error;
    } finally {
      if (streamController.current === controller) streamController.current = null;
    }
  }
  async function refresh() {
    const value = await detail.refetch();
    if (value.error) throw value.error;
  }
  function newConversation() {
    void submission.submit((key) => protect(() => createConversation("新对话", key)), async (value) => {
      selectConversation(value.id); setPage(1); setBlocked(false); setAccessError(null); setDeleteCheck(false); showPanel("chat");
      await qc.invalidateQueries({ queryKey: [...base, "list"] });
      void status.refetch();
    });
  }
  function ask(value: string) {
    if (!id || !value.trim() || locked || submission.isLocked() || unavailable || !status.data?.configured) return;
    const body = { message: value.trim(), selected_ids: [] };
    void submission.submit((key) => protect(() => streamed(id, (options) => sendMessage(id, body, key, options))), (turn) => {
      setQuestions((old) => ({ ...old, [id]: "" })); acceptTurn(turn);
    });
  }
  function openContext(turn: Turn) { setContextTurn(turn.id); showPanel("context"); }
  const modelName = status.data?.configured ? `${status.data.provider_name} · ${status.data.model}` : "尚未配置模型";

  return <section aria-label="AI 助手" className="assistant-experience flex h-full min-h-0 min-w-0 flex-col">
    {/* Keep stage announcements outside the busy transcript, which intentionally
        defers full reply announcements until the response is complete. */}
    <p role="status" aria-label="助手处理状态" aria-live="polite" aria-atomic="true" className="sr-only">{activityAnnouncement}</p>
    <div className="flex h-12 shrink-0 items-center justify-between gap-2 border-b border-border/70 px-3 xl:hidden">
      <Button size="sm" variant="ghost" aria-label="历史会话" aria-pressed={panel === "history"} onClick={() => showPanel(panel === "history" ? "chat" : "history")}><History className="size-4" aria-hidden />会话</Button>
      {panel !== "chat" && <Button size="sm" variant="ghost" onClick={() => showPanel("chat")}><ArrowLeft className="size-4" aria-hidden />返回对话</Button>}
      <Button size="sm" variant="ghost" aria-label="业务依据与草稿" aria-pressed={panel === "context"} onClick={() => showPanel(panel === "context" ? "chat" : "context")}><ClipboardList className="size-4" aria-hidden />依据与草稿{pendingCount > 0 && <span className="rounded-full bg-primary px-1.5 text-primary-foreground">{pendingCount}</span>}</Button>
    </div>
    {(submission.error || submission.notice || unavailable) && <section aria-label="操作反馈" className="max-h-40 shrink-0 space-y-2 overflow-y-auto border-b border-border/60 bg-background px-4 py-3 text-sm">
      <SubmissionFeedback value={submission} onRefresh={() => void refresh()} />
      {unavailable && <div role="alert" className="rounded-xl bg-amber-500/10 p-3 text-sm text-amber-900 dark:text-amber-200">
        {historyUnavailable ? "此对话已删除或聊天正文已到期，旧内容已停止展示。请新建对话后继续。"
          : accessError?.status === 401 || detail.error instanceof AssistantError && detail.error.status === 401
          ? <>登录已失效，旧对话已停止展示。<Link href="/login" className="ml-2 underline">重新登录</Link></>
          : "权限或模型服务已变化，旧对话已停止展示。请新建对话后继续。"}
      </div>}

    </section>}
    <ChatApp open={false} openMobile={false} className="assistant-grid flex-1 rounded-none border-0" data-panel={panel} data-context-open={contextOpen}>
      <aside data-assistant-panel="history" aria-label="历史会话" className="assistant-history min-h-0 min-w-0 flex-col border-r border-border/70 bg-muted/20">
        <h2 id="assistant-history-heading" tabIndex={-1} className="sr-only">历史会话</h2>
        <ConversationHistory items={conversations.data?.items ?? []} selectedId={id} page={page}
          total={conversations.data?.total ?? 0} loading={conversations.isPending} error={!!conversations.error}
          locked={submission.locked} onNew={newConversation} onPage={setPage}
          onSelect={(value) => { selectConversation(value); setBlocked(false); setDeleteCheck(false); showPanel("chat"); }} />
        {conversations.error && <Button variant="ghost" className="m-3" onClick={() => void conversations.refetch()}>重新读取对话</Button>}
      </aside>
      <section aria-label="助手对话" data-assistant-panel="chat" className="assistant-chat min-h-0 min-w-0 flex-col bg-background">
        <header className="flex min-h-16 shrink-0 items-center gap-3 border-b border-border/60 px-4 py-3 sm:px-6">
          <span className="grid size-8 shrink-0 place-items-center rounded-full border border-border/60"><Sparkles className="size-4" aria-hidden /></span>
          <div className="min-w-0 flex-1"><h2 id="assistant-chat-heading" tabIndex={-1} className="truncate text-sm font-medium outline-none">{conversation?.title ?? "经营助手"}</h2>
            <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground"><span aria-hidden className={`size-1.5 rounded-full ${running || submission.busy ? "bg-amber-400" : status.data?.configured ? "bg-emerald-500" : "bg-muted-foreground/40"}`} />{submission.uncertain ? "连接中断，等待确认处理结果" : running || submission.busy ? "正在处理你的请求" : "查业务、聊想法，一起把事情做好"}</p>
          </div>
          <Button variant="ghost" size="icon" aria-label="切换业务依据与草稿" aria-expanded={contextOpen}
            className="hidden xl:inline-flex" onClick={() => { if (contextOpen) { setContextOpen(false); showPanel("chat"); } else showPanel("context"); }}>
            {contextOpen ? <PanelRightClose className="size-4" /> : <PanelRightOpen className="size-4" />}
          </Button>
          <details className="assistant-actions relative shrink-0">
            <summary aria-label="对话操作" className="grid size-9 cursor-pointer list-none place-items-center rounded-full hover:bg-muted focus-visible:outline-2"><MoreHorizontal className="size-4" /></summary>
            <div className="absolute right-0 top-11 z-20 w-64 rounded-2xl border border-border bg-background p-2 shadow-xl">
              <Button variant="ghost" className="w-full justify-start" disabled={submission.busy} onClick={() => { void detail.refetch(); void conversations.refetch(); void status.refetch(); }}><RefreshCw className="size-4" aria-hidden />重新读取记录</Button>
              {status.data?.can_manage_provider && permissions.includes("ai.provider.manage") && <Link href="/settings/llm" className="block rounded-full px-4 py-2.5 text-sm hover:bg-muted">管理模型服务</Link>}
              <Button variant="ghost" className="w-full justify-start text-destructive" disabled={!id || locked || unavailable} onClick={() => setDeleteCheck(!deleteCheck)}>删除对话</Button>
              {deleteCheck && <div className="space-y-3 p-3 text-xs leading-5"><p>删除聊天正文及未执行提案，已创建单据仍然保留。</p>
                <Button variant="destructive" disabled={locked} onClick={() => {
                  if (!id) return;
                  void submission.submit((key) => protect(() => deleteConversation(id, key)), async () => {
                    qc.setQueryData<ConversationsPage>([...base, "list", page], (old) => old ? {
                      ...old, items: old.items.filter((item) => item.id !== id), total: Math.max(0, old.total - 1),
                    } : old);
                    selectConversation(null); setDeleteCheck(false); qc.removeQueries({ queryKey: detailKey });
                    setQuestions((old) => { const next = { ...old }; delete next[id]; return next; });
                    await conversations.refetch();
                  });
                }}>确认删除对话</Button></div>}
            </div>
          </details>
        </header>
        <MessageScroller key={id ?? "empty"} label="对话记录" busy={running || submission.busy}
          followOutput className="min-h-0 flex-1" viewportClassName="px-4 py-8 sm:px-8"
          viewportProps={{ tabIndex: 0 }} contentClassName="mx-auto w-full max-w-3xl min-h-full">
          {!id && conversations.isPending && <p role="status" className="text-sm text-muted-foreground">正在读取对话…</p>}
          {!!id && detail.isPending && !unavailable && <p role="status" className="text-sm text-muted-foreground">正在读取对话记录…</p>}
          {detail.error && !unavailable && <div role="alert" className="space-y-3 text-sm"><p>{detail.error.message} 请重新读取记录。</p><Button variant="outline" onClick={() => void detail.refetch()}>重新读取记录</Button></div>}
          {conversation?.older_turns_omitted && <p className="mb-4 text-center text-xs text-muted-foreground">仅展示最近的对话记录。</p>}
          {!turns.length && !submission.busy && (!id || !detail.isPending) && !unavailable && <div className="mx-auto flex min-h-full max-w-lg flex-col justify-center py-5">
            <span className="mb-5 grid size-12 place-items-center rounded-2xl border border-border bg-muted/50"><Sparkles className="size-6" aria-hidden /></span>
            <p className="mb-2 text-xs font-medium tracking-wider text-muted-foreground">FORGE · 经营助手</p>
            <h3 className="text-2xl font-medium tracking-tight sm:text-3xl">今天，有什么需要一起处理？</h3>
            <p className="mt-3 text-sm leading-7 text-muted-foreground">查一件商品，理一理经营情况，或准备一份订单。也可以先聊聊你的想法。</p>
            {!id && <Button className="mt-5 self-start" disabled={submission.locked || conversations.isPending} onClick={newConversation}>开始新对话</Button>}
            <div className="mt-7 grid grid-cols-1 gap-2.5 sm:grid-cols-2">{starters.filter((item) => permissions.includes(item.permission)).map(({ title, description, text, icon: Icon }) => <button key={title}
              aria-label={title} disabled={!id || locked || unavailable || !status.data?.configured}
              onClick={() => { setQuestion(text); document.getElementById("assistant-question")?.focus(); }}
              className="group rounded-2xl border border-border/70 p-4 text-left transition-colors hover:bg-muted/50 focus-visible:outline-2 disabled:opacity-45">
              <Icon aria-hidden className="mb-3 size-4 text-muted-foreground" /><span className="block text-sm font-medium">{title}</span><span className="mt-1.5 block text-xs leading-5 text-muted-foreground">{description}</span>
            </button>)}</div>
          </div>}
          <MessageGroup spacing="default">
            {turns.map((turn) => <div key={turn.id} className="min-w-0 space-y-7 pb-7">
              <Message from="user" aria-label="你的消息">
                <MessageContent><MessageHeader><span>你</span><time>{when(turn.created_at)}</time></MessageHeader>
                  <MessageBubble variant="solid" animateIn={false} className="max-w-[88%] rounded-3xl"><MessageBubbleContent><p className="whitespace-pre-wrap break-words text-sm leading-6">{turn.prompt}</p></MessageBubbleContent></MessageBubble>
                </MessageContent>
              </Message>
              <Message from="assistant" aria-label="经营助手回复"><MessageAvatar aria-hidden className="border border-border/60 bg-transparent"><Sparkles /></MessageAvatar>
                <MessageContent className="gap-4"><MessageHeader>Forge 助手</MessageHeader>
                  <TurnActivity turn={turn} interrupted={submission.uncertain && turn.state === "RUNNING"} />
                  {turn.answer && <div className="w-full min-w-0 px-1 text-sm leading-7" aria-label={turn.state === "RUNNING" ? "正在生成的回复" : "完整回复"}>
                    <p className="whitespace-pre-wrap break-words">{turn.answer}</p>
                    {turn.state === "RUNNING" && <span className="mt-2 block text-[11px] text-muted-foreground">回复接收中…</span>}
                  </div>}
                  {turn.error_message && <p role="alert" className="rounded-2xl bg-amber-500/10 p-3 text-sm text-amber-900 dark:text-amber-200">{turn.error_message}</p>}
                  {turn.can_retry && <Button variant="outline" disabled={submission.locked || unavailable} onClick={() => {
                    if (!id) return;
                    void submission.submit((key) => protect(() => streamed(id, (options) => retryTurn(turn.id, key, options))), acceptTurn);
                  }}>恢复本次处理</Button>}
                  <BusinessResultCards turn={turn} onReview={() => openContext(turn)} />
                  {turn.guided && turn.state === "COMPLETED" && <div aria-label="继续处理业务" className="flex flex-wrap gap-2">
                    {starters.filter((item) => permissions.includes(item.permission)).slice(0, 3).map((item) => <Button
                      key={item.title} size="sm" variant="outline" disabled={locked || unavailable}
                      onClick={() => { setQuestion(item.text); document.getElementById("assistant-question")?.focus(); }}>
                      <item.icon aria-hidden className="size-3.5" />{item.title}
                    </Button>)}
                  </div>}
                  <div className="flex flex-wrap items-center gap-2">
                    {turn.answer && turn.state !== "RUNNING" && <Button size="icon" variant="ghost" aria-label="复制回复" className="size-8" onClick={async () => {
                      try { await navigator.clipboard.writeText(turn.answer!); setCopyStatus({ id: turn.id, ok: true }); }
                      catch { setCopyStatus({ id: turn.id, ok: false }); }
                    }}>{copyStatus?.id === turn.id && copyStatus.ok ? <CheckCheck className="size-3.5" /> : <Copy className="size-3.5" />}</Button>}
                    {copyStatus?.id === turn.id && <span role="status" className="text-xs text-muted-foreground">{copyStatus.ok ? "已复制" : "无法访问剪贴板，请选中文字复制。"}</span>}
                  </div>
                </MessageContent>
              </Message>
            </div>)}
            {submission.busy && !liveTurn && !running && <p role="status" className="py-4 text-xs text-muted-foreground"><ThinkingShimmer>正在连接助手…</ThinkingShimmer></p>}
          </MessageGroup>
        </MessageScroller>
        <section aria-label="发送消息" className="mx-auto w-full max-w-3xl shrink-0 space-y-2 bg-background px-3 pb-3 pt-2 sm:px-6 sm:pb-5">
          <div className="space-y-2 text-sm">
            {!status.isPending && !status.data?.configured && !status.error && <p role="status">对话模型尚未配置。
              {status.data?.can_manage_provider && permissions.includes("ai.provider.manage")
                ? <Link href="/settings/llm" className="ml-2 underline">配置模型服务</Link> : "请联系管理员配置后再提问。"}</p>}
            {status.error && <p role="alert">暂时无法读取模型状态。</p>}
          </div>
          {!!id && <details className="group rounded-xl bg-muted/25 px-3 py-2 text-xs">
            <summary className="flex cursor-pointer list-none items-center gap-2 text-muted-foreground"><CalendarDays className="size-3.5" aria-hidden />每日简报<ChevronDown className="ml-auto size-3 group-open:rotate-180" aria-hidden /></summary>
            <div className="mt-3 flex flex-wrap items-end gap-2"><label className="min-w-0 flex-1">简报日期<Input aria-label="简报日期" type="date" value={day} disabled={locked || unavailable} className="mt-1 h-9"
              onChange={(event) => setDay(event.target.value)} /></label>
              <Button size="sm" variant="outline" disabled={locked || unavailable || !permissions.includes("dashboard.read")} onClick={() => {
                const selectedDay = day || undefined;
                void submission.submit((key) => protect(() => streamed(id, (options) => requestBrief(id, selectedDay, key, options))), acceptTurn);
              }}>生成每日简报</Button>
              <p className="w-full leading-5 text-muted-foreground">{permissions.includes("dashboard.read") ? "留空使用上一个完整业务日" : "生成简报需要经营概览权限"}</p>
            </div>
          </details>}
          <label htmlFor="assistant-question" className="sr-only">你的问题</label>
          <PromptInput id="assistant-question" aria-label="你的问题" sendLabel="发送问题" maxLength={4000} value={question}
            disabled={!id || locked || unavailable || !status.data?.configured}
            minRows={2} maxRows={5} className="rounded-3xl border-border/80 bg-muted/15 p-3 focus-within:bg-muted/25"
            leadingAction={<span className="max-w-[min(260px,60vw)] truncate px-1 text-[11px] text-muted-foreground" title={modelName}>{modelName}</span>}
            placeholder="问业务，聊想法，或从一份订单开始…" onValueChange={setQuestion} onSubmit={ask} />
          <p className="px-1 text-[10px] leading-4 text-muted-foreground"><span className="hidden sm:inline">Enter 发送 · Shift + Enter 换行。 </span>金额与数量以业务记录为准，创建草稿前由你确认。</p>
        </section>
      </section>
      <aside data-assistant-panel="context" aria-label="业务依据与草稿" className="assistant-context min-h-0 min-w-0 flex-col border-l border-border/70 bg-muted/15">
        <header className="flex min-h-16 shrink-0 items-center gap-2 border-b border-border/60 px-4 py-3"><ClipboardList className="size-4 text-muted-foreground" aria-hidden /><h2 id="assistant-context-heading" tabIndex={-1} className="text-sm font-medium outline-none">业务依据与草稿</h2>
          {pendingCount > 0 && <span className="ml-auto rounded-full bg-amber-500/10 px-2 py-1 text-[10px] text-amber-800 dark:text-amber-200">{pendingCount} 项待复核</span>}
          <Button size="icon" variant="ghost" aria-label="收起业务依据与草稿" className="ml-auto hidden size-8 xl:inline-flex" onClick={() => { setContextOpen(false); showPanel("chat"); }}><PanelRightClose className="size-4" /></Button>
        </header>
        {contextTurns.length > 1 && <div className="shrink-0 border-b border-border/60 p-3"><label className="text-xs text-muted-foreground">对应消息<select aria-label="对应消息" className="mt-2 h-10 w-full min-w-0 rounded-xl border border-border bg-background px-2 text-sm text-foreground" value={focused?.id ?? ""} onChange={(event) => setContextTurn(event.target.value)}>
          {contextTurns.map((turn, index) => <option key={turn.id} value={turn.id}>{index + 1}. {turn.prompt.slice(0, 45)}</option>)}
        </select></label></div>}
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3">
          {readFailed && !unavailable && <div role="alert" className="mb-4 rounded-xl bg-amber-500/10 p-3 text-xs leading-6 text-amber-900 dark:text-amber-200">业务记录暂时无法刷新，当前编辑已保留。恢复读取前暂停创建草稿。<Button size="sm" variant="outline" className="mt-2" onClick={() => { void detail.refetch(); void status.refetch(); }}>重新读取记录</Button></div>}
          {!focused && <div className="flex h-full min-h-60 flex-col items-center justify-center p-5 text-center"><span className="mb-4 grid size-11 place-items-center rounded-2xl border border-border bg-background"><Database className="size-5 text-muted-foreground" aria-hidden /></span><h3 className="text-sm font-medium">让每个结论都有依据</h3><p className="mt-2 text-xs leading-6 text-muted-foreground">查询结果、来源记录和待复核的单据会显示在这里。选择回复中的入口即可查看。</p></div>}
          {contextTurns.map((turn) => <div key={turn.id} hidden={turn.id !== focused?.id} className="space-y-4">
            <p className="border-l-2 border-border pl-3 text-xs leading-6 text-muted-foreground">{turn.prompt}</p>
            {turn.proposal && <ProposalCard key={`${turn.proposal.id}:${turn.proposal.revision}:${turn.proposal.status}`}
              proposal={turn.proposal} permissions={permissions} submission={submission} blocked={unavailable || running || readFailed}
              protect={protect} onChanged={refresh} />}
            {(turn.evidence ?? []).map((item) => <EvidenceCard key={item.id} value={item} />)}
          </div>)}
        </div>
      </aside>
    </ChatApp>
  </section>;
}
