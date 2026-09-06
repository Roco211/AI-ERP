"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, Bot, CalendarDays, Database, MessageSquare, Plus, Sparkles, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApprovalCard } from "@/components/agents/approval-card";
import { Message, MessageAvatar, MessageBubble, MessageBubbleContent, MessageContent,
  MessageGroup, MessageHeader, MessageTyping } from "@/components/agents/message";
import { MessageScroller } from "@/components/agents/message-scroller";
import { PromptInput } from "@/components/agents/prompt-input";
import { ProductPicker } from "@/features/catalog/picker";
import { resourceClients, unwrap } from "@/features/catalog/client";
import { useOperationSubmission } from "@/features/operations/use-submission";
import { SubmissionFeedback, selectClass } from "@/features/operations/presentation";
import {
  AssistantError, getAssistantStatus, listConversations, getConversation, createConversation,
  deleteConversation, sendMessage, requestBrief, editProposal, approveProposal, rejectProposal,
  retryTurn, safeSourceHref,
  type Evidence, type Conversation, type Turn, type Proposal, type Preview, type Receipt,
  type DraftInput,
} from "./assistant-client";

type Submission = ReturnType<typeof useOperationSubmission>;
const hasAll = (permissions: string[], required: string[]) => required.every((p) => permissions.includes(p));
const authorityError = (error: unknown) => error instanceof AssistantError && (
  [401, 403].includes(error.status) || error.status === 409 &&
  /AUTHORITY|PERMISSION|PROVIDER|CONTEXT/.test(error.code ?? "")
);
const when = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });
const PRICE_SOURCE_LABELS: Record<string, string> = { standard: "标准价", customer: "客户专价",
  history: "有效成交历史", retail: "零售价", wholesale: "批发价", manual: "手工复核",
};
const priceSourceLabel = (value: unknown) => PRICE_SOURCE_LABELS[String(value)] ?? "当前报价规则";
const approvalStatusLabels = { pending: "等待你复核", submitting: "正在提交", approved: "已创建草稿" };

export function AssistantWorkspace({ permissions, identityKey = "current" }: {
  permissions: string[]; identityKey?: string;
}) {
  if (!permissions.includes("ai.use")) return <p role="alert" className="mt-8 text-sm">
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
  const [question, setQuestion] = useState("");
  const [day, setDay] = useState("");
  const [blocked, setBlocked] = useState(false);
  const [accessError, setAccessError] = useState<AssistantError | null>(null);
  const [deleteCheck, setDeleteCheck] = useState(false);
  const submission = useOperationSubmission();
  const base = ["assistant", cacheScope];
  const status = useQuery({ queryKey: [...base, "status"], queryFn: ({ signal }) => getAssistantStatus(signal),
    retry: false, gcTime: 0 });
  const conversations = useQuery({ queryKey: [...base, "list", page],
    queryFn: ({ signal }) => listConversations(page, signal), retry: false, gcTime: 0 });
  const id = chosen ?? conversations.data?.items[0]?.id;
  const detailKey = [...base, "conversation", id];
  const detail = useQuery({ queryKey: detailKey,
    queryFn: ({ signal }) => getConversation(id!, signal), enabled: !!id && !blocked,
    retry: false, gcTime: 0,
    refetchInterval: (query) => query.state.data?.turns.some((turn) => turn.state === "RUNNING") ? 2500 : false,
  });
  const unavailable = blocked || authorityError(detail.error) || authorityError(status.error);
  const conversation = detail.error || unavailable ? undefined : detail.data;
  const running = conversation?.turns.some((turn) => turn.state === "RUNNING") ?? false;
  const locked = submission.locked || running;
  function selectConversation(value: string | null) {
    setChosen(value);
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
    void detail.refetch();
  }
  async function refresh() {
    const value = await detail.refetch();
    if (value.error) throw value.error;
  }
  function newConversation() {
    void submission.submit((key) => protect(() => createConversation("新对话", key)), async (value) => {
      selectConversation(value.id); setBlocked(false); setAccessError(null); setQuestion(""); setDeleteCheck(false);
      await qc.invalidateQueries({ queryKey: [...base, "list"] });
      void status.refetch();
    });
  }
  function ask(value: string) {
    if (!id || !value.trim() || locked || unavailable || !status.data?.configured) return;
    const body = { message: value.trim(), selected_ids: [] };
    void submission.submit((key) => protect(() => sendMessage(id, body, key)), (turn) => {
      setQuestion(""); acceptTurn(turn);
    });
  }
  return <section aria-label="AI 助手" className="grid min-w-0 gap-6 xl:grid-cols-[224px_minmax(0,1fr)]">
    <aside className="min-w-0 rounded-3xl bg-muted/35 p-4 xl:self-start">
      <div className="flex items-center justify-between gap-2"><h2 className="text-sm font-medium">我的对话</h2>
        <Button size="sm" variant="ghost" disabled={submission.locked} onClick={newConversation}>
          <Plus aria-hidden className="size-4" />新对话
        </Button></div>
      <p className="mt-3 text-xs leading-5 text-muted-foreground">对话仅自己可见，正文保留 7 天。</p>
      {conversations.isPending && <p role="status">正在读取对话…</p>}
      {conversations.error && <p role="alert">无法读取对话列表，请重新读取。</p>}
      <nav aria-label="对话列表" className="mt-4 flex gap-2 overflow-x-auto pb-1 xl:max-h-[60dvh] xl:flex-col xl:overflow-y-auto">
        {conversations.data?.items.map((item) => <button key={item.id}
          aria-current={item.id === id ? "page" : undefined} disabled={submission.locked}
          className={`min-w-40 rounded-2xl border p-3 text-left text-sm transition-colors xl:w-full xl:min-w-0 ${item.id === id ? "border-border bg-background" : "border-transparent hover:bg-muted/70"}`}
          onClick={() => { selectConversation(item.id); setBlocked(false); setDeleteCheck(false); }}>
          <span className="block truncate"><MessageSquare className="mr-1 inline size-3" aria-hidden />{item.title}</span>
          <time className="mt-1 block text-xs text-muted-foreground">{when(item.created_at)}</time>
        </button>)}
      </nav>
      {!conversations.isPending && !conversations.data?.items.length && <p className="text-sm text-muted-foreground">还没有对话，创建一个开始。</p>}
      {(conversations.data?.total ?? 0) > 20 && <div className="flex gap-2">
        <Button variant="outline" disabled={page <= 1 || submission.locked} onClick={() => setPage(page - 1)}>上一页</Button>
        <Button variant="outline" disabled={page * 20 >= (conversations.data?.total ?? 0) || submission.locked}
          onClick={() => setPage(page + 1)}>下一页</Button>
      </div>}
    </aside>
    <div className="min-w-0 space-y-5">
      <header className="flex flex-wrap items-center gap-3 px-1">
        <span className="grid size-11 shrink-0 place-items-center rounded-2xl border border-border/70 bg-muted/50"><Sparkles className="size-5" aria-hidden /></span>
        <div className="min-w-0 flex-1"><h2 className="text-lg font-medium tracking-tight">经营助手</h2>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">查询业务记录，复核后创建销售或采购草稿。</p></div>
        {status.data?.configured ? <p className="max-w-full break-all rounded-full border border-border/70 px-3 py-1.5 text-xs text-muted-foreground">{status.data.provider_name} · {status.data.model}</p>
          : !status.isPending && <p role="status" className="mt-3 text-sm">对话模型尚未配置。
            {status.data?.can_manage_provider && permissions.includes("ai.provider.manage")
              ? <Link href="/settings/llm" className="ml-2 text-primary underline">配置模型服务</Link>
              : "请联系管理员配置后再提问。"}</p>}
        {status.error && <p role="alert" className="mt-3">暂时无法读取模型状态。</p>}
      </header>
      <SubmissionFeedback value={submission} onRefresh={() => void refresh()} />
      {submission.busy && <p role="status" className="text-sm">正在提交并等待处理结果…</p>}
      {unavailable && <div role="alert" className="rounded-2xl bg-amber-500/10 p-4 text-sm text-amber-900 dark:text-amber-200">
        {accessError?.status === 401 || detail.error instanceof AssistantError && detail.error.status === 401
          ? <>登录已失效，旧对话已停止展示。<Link href="/login" className="ml-2 underline">重新登录</Link></>
          : "权限或模型服务已变化，旧对话已停止展示。请新建对话后继续。"}
      </div>}
      {!!id && <div className="flex flex-wrap items-end gap-3 rounded-2xl border border-border/70 bg-muted/20 p-4">
        <label className="text-sm">简报日期<Input aria-label="简报日期" type="date" value={day} disabled={locked || unavailable}
          onChange={(event) => setDay(event.target.value)} /></label>
        <Button variant="outline" disabled={locked || unavailable || !permissions.includes("dashboard.read")} onClick={() => {
          const selectedDay = day || undefined;
          void submission.submit((key) => protect(() => requestBrief(id, selectedDay, key)), acceptTurn);
        }}><CalendarDays aria-hidden className="size-4" />生成每日简报</Button>
        <span className="text-xs text-muted-foreground">{permissions.includes("dashboard.read") ? "留空使用上一个完整业务日" : "生成简报需要经营概览权限"}</span>
        <Button variant="ghost" disabled={submission.busy} onClick={() => { void detail.refetch(); void conversations.refetch(); void status.refetch(); }}>重新读取记录</Button>
        <Button variant="ghost" disabled={locked || unavailable} onClick={() => setDeleteCheck(!deleteCheck)}>删除对话</Button>
        {deleteCheck && <div className="w-full space-y-2 text-sm"><p>删除聊天正文及未执行提案，已创建单据仍然保留。</p>
          <Button variant="destructive" disabled={locked} onClick={() => {
            void submission.submit((key) => protect(() => deleteConversation(id, key)), async () => {
              selectConversation(null); setDeleteCheck(false); qc.removeQueries({ queryKey: detailKey });
              await conversations.refetch();
            });
          }}>确认删除对话</Button></div>}
      </div>}
      {!!id && detail.isPending && !unavailable && <p role="status">正在读取对话记录…</p>}
      {detail.error && !unavailable && <p role="alert">{detail.error.message} 请重新读取记录。</p>}
      {conversation?.older_turns_omitted && <p className="text-xs text-muted-foreground">仅展示最近的对话记录。</p>}
      <div className="min-w-0 overflow-hidden rounded-3xl border border-border/70 bg-background">
      <MessageScroller key={id ?? "empty"} label="对话记录" busy={running || submission.busy}
        followOutput={!conversation?.turns.some((turn) => turn.proposal?.status === "PENDING")}
        className="h-[clamp(16rem,calc(100dvh-34rem),34rem)] min-h-64" viewportClassName="px-3 py-5 sm:px-5"
        viewportProps={{ tabIndex: 0 }} contentClassName="min-h-full">
        {!conversation?.turns.length && (!id || !detail.isPending) && !unavailable && <div className="mx-auto flex min-h-60 max-w-md flex-col items-center justify-center px-4 text-center">
          <span className="mb-4 grid size-12 place-items-center rounded-2xl bg-muted"><Bot className="size-6" aria-hidden /></span>
          <h3 className="text-lg font-medium">从一个业务问题开始</h3>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">{id ? "可以查询商品和库存，或说明客户、商品、数量，准备一份待复核的草稿。" : "新建对话后，开始查询商品、库存与经营记录。"}</p>
        </div>}
        <MessageGroup spacing="default">
        {conversation?.turns.map((turn) => <div key={turn.id} className="min-w-0 space-y-4">
          <Message from="user" aria-label="你的消息"><MessageAvatar aria-hidden><User /></MessageAvatar>
            <MessageContent><MessageHeader><span>你</span><time>{when(turn.created_at)}</time></MessageHeader>
              <MessageBubble variant="solid" animateIn={false}><MessageBubbleContent><p className="whitespace-pre-wrap break-words text-sm">{turn.prompt}</p></MessageBubbleContent></MessageBubble>
            </MessageContent>
          </Message>
          <Message from="assistant" aria-label="经营助手回复"><MessageAvatar aria-hidden><Bot /></MessageAvatar>
          <MessageContent className="gap-4"><MessageHeader>经营助手</MessageHeader>
          {turn.state === "RUNNING" && <div role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><MessageTyping label="正在处理" /><span>正在查询并核对依据…</span></div>}
          {turn.answer && <MessageBubble variant="soft" animateIn={false}><MessageBubbleContent><p className="whitespace-pre-wrap break-words text-sm">{turn.answer}</p></MessageBubbleContent></MessageBubble>}
          {turn.error_message && <p role="alert" className="rounded-2xl bg-amber-500/10 p-3 text-sm text-amber-900 dark:text-amber-200">{turn.error_message}</p>}
          {turn.can_retry && <Button variant="outline" disabled={submission.locked || unavailable} onClick={() => {
            void submission.submit((key) => protect(() => retryTurn(turn.id, key)), acceptTurn);
          }}>恢复本次处理</Button>}
          {(turn.evidence ?? []).map((item) => <EvidenceCard key={item.id} value={item} />)}
          </MessageContent></Message>
          {turn.proposal && <ProposalCard key={`${turn.proposal.id}:${turn.proposal.revision}:${turn.proposal.status}`}
            proposal={turn.proposal} permissions={permissions} submission={submission} blocked={unavailable || running}
            protect={protect} onChanged={refresh} />}
        </div>)}
        </MessageGroup>
      </MessageScroller>
      <div className="border-t border-border/60 bg-background p-3 sm:p-4">
        <label htmlFor="assistant-question" className="sr-only">你的问题</label>
        <PromptInput id="assistant-question" aria-label="你的问题" sendLabel="发送问题" maxLength={4000} value={question}
          disabled={!id || locked || unavailable || !status.data?.configured}
          minRows={2} maxRows={6} className="border-0 bg-muted/45 focus-within:bg-muted/70"
          placeholder="例如：M8 螺栓还有多少可用库存？" onValueChange={setQuestion} onSubmit={ask} />
        <p className="mt-3 px-2 text-xs leading-5 text-muted-foreground">Enter 发送 · Shift + Enter 换行。候选不明确时请补充编码；金额、数量以业务记录为准。</p>
      </div>
      </div>
    </div>
  </section>;
}

function EvidenceCard({ value }: { value: Evidence }) {
  return <section aria-label={value.title} className="w-full min-w-0 space-y-4 rounded-2xl border border-border/70 bg-background p-4 sm:p-5">
    <h3 className="flex items-center gap-2 text-sm font-medium"><Database className="size-4 text-muted-foreground" aria-hidden />{value.title}</h3>
    <p className="text-xs text-muted-foreground">查询时间：{when(value.as_of)} · {value.scope}</p>
    <dl className="grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-3">{value.summary.map((fact, index) =>
      <div key={index}><dt className="text-muted-foreground">{fact.label}</dt>
        <dd className="mt-1 whitespace-pre-wrap break-words tabular-nums">{fact.value}{fact.unit ? ` ${fact.unit}` : ""}</dd></div>)}</dl>
    {!!value.columns.length && <div className="overflow-auto"><table className="w-full text-left text-sm">
      <thead><tr>{value.columns.map((column) => <th key={column} className="whitespace-nowrap border-b p-2 font-medium">{column}</th>)}</tr></thead>
      <tbody>{value.rows.map((row, index) => <tr key={index}>{value.columns.map((column) =>
        <td key={column} className="max-w-xs whitespace-pre-wrap break-words border-b p-2 tabular-nums">{row[column] ?? "—"}</td>)}</tr>)}</tbody>
    </table></div>}
    {value.truncated && <p className="text-xs text-muted-foreground">当前只展示部分记录，可补充条件缩小范围。</p>}
    <div className="flex flex-wrap gap-3 text-sm">{value.links.filter((link) => safeSourceHref(link.href)).map((link) =>
      <a className="inline-flex items-center gap-1 text-foreground underline decoration-border underline-offset-4" key={link.href} href={link.href}>{link.label}<ArrowUpRight className="size-3.5" aria-hidden /></a>)}</div>
  </section>;
}

type EditableLine = {
  product_id: string; unit_id: string; product_label: string; unit_label: string;
  qty: string; pricing_mode: "AUTO" | "MANUAL"; unit_price: string;
};
function EntitySelect({ resource, label, value, currentName, disabled, allowed, onChange }: {
  resource: "customers" | "suppliers" | "warehouses"; label: string; value: string;
  currentName: string; disabled: boolean; allowed: boolean; onChange: (value: string) => void;
}) {
  const [q, setQ] = useState("");
  const data = useQuery({ queryKey: ["assistant-entity", resource, q],
    queryFn: async () => unwrap(await resourceClients[resource].list({ q, active: true, page_size: 25 })),
    enabled: allowed, gcTime: 0, retry: false });
  return <div className="space-y-2"><label className="block text-sm">{label}
    <select aria-label={label} className={`${selectClass} mt-1 w-full`} value={value}
      disabled={disabled || !allowed} onChange={(event) => onChange(event.target.value)}>
      {!data.data?.items.some((item) => item.id === value) && <option value={value}>{currentName}</option>}
      {data.data?.items.map((item) => <option key={item.id} value={item.id}>{String(item.code)} · {String(item.name)}</option>)}
    </select></label>
    {allowed && <Input aria-label={`搜索${label}`} placeholder={`按编码或名称搜索${label}`} value={q}
      disabled={disabled} onChange={(event) => setQ(event.target.value)} />}
    {data.error && <p role="alert" className="text-xs">暂时无法读取{label}选项。</p>}
  </div>;
}

function ProposalCard({ proposal, permissions, submission, blocked, protect, onChanged }: {
  proposal: Proposal; permissions: string[]; submission: Submission; blocked: boolean;
  protect: <T>(operation: () => Promise<T>) => Promise<T>; onChanged: () => Promise<void>;
}) {
  const [receipt, setReceipt] = useState<Receipt | null>(proposal.receipt ?? null);
  if (receipt) return <section aria-label="草稿创建结果" className="w-full min-w-0">
    <ApprovalCard status="approved" statusLabels={approvalStatusLabels} title="草稿已创建" result={<>
      <span className="block">已保存为草稿，可进入业务页面继续复核。</span>
      {safeSourceHref(receipt.href) && <a className="mt-3 inline-flex items-center gap-1 text-foreground underline underline-offset-4" href={receipt.href}>查看已创建草稿<ArrowUpRight aria-hidden className="size-3.5" /></a>}
    </>} />
  </section>;
  if (proposal.status !== "PENDING" || !proposal.preview) return <p className="rounded-lg border p-4 text-sm">
    {proposal.status === "REJECTED" ? "已取消此提案。" : proposal.status === "EXPIRED" ? "提案已过期，请重新提出开单请求。" : "此提案暂时不可复核。"}
  </p>;
  return <DraftEditor proposal={proposal} initial={proposal.preview} permissions={permissions}
    submission={submission} blocked={blocked} protect={protect} onChanged={onChanged} onCreated={setReceipt} />;
}

function DraftEditor({ proposal, initial, permissions, submission, blocked, protect, onChanged, onCreated }: {
  proposal: Proposal; initial: Preview; permissions: string[]; submission: Submission; blocked: boolean;
  protect: <T>(operation: () => Promise<T>) => Promise<T>; onChanged: () => Promise<void>;
  onCreated: (value: Receipt) => void;
}) {
  const [preview, setPreview] = useState(initial);
  const [revision, setRevision] = useState(proposal.revision);
  const sales = proposal.kind === "SALES";
  const [party, setParty] = useState("customer_id" in initial.order ? initial.order.customer_id : initial.order.supplier_id);
  const [warehouse, setWarehouse] = useState(initial.order.warehouse_id);
  const [reason, setReason] = useState(initial.order.reason);
  const [lines, setLines] = useState<EditableLine[]>(() => initial.lines.map((line) => {
    const original = initial.order.lines.find((input) => input.product_id === line.product_id);
    return { product_id: line.product_id, unit_id: line.unit_id, product_label: line.product_label,
      unit_label: line.unit_label, qty: String(original?.qty ?? line.qty),
      pricing_mode: sales && original && "pricing_mode" in original ? original.pricing_mode ?? "AUTO" : "MANUAL",
      unit_price: String(line.unit_price) };
  }));
  const [dirty, setDirty] = useState(false);
  const [picker, setPicker] = useState<number | "add" | null>(null);
  const [error, setError] = useState("");
  const [mustPreview, setMustPreview] = useState(false);
  const allowed = hasAll(permissions, ["ai.use", "ai.draft.create", "catalog.read", "warehouse.read",
    sales ? "sales.read" : "purchase.read", sales ? "customer.read" : "supplier.read",
    sales ? "sales.order.write" : "purchase.order.write", sales ? "product.price.read" : "product.cost.read"]);
  const locked = submission.locked || blocked || !allowed;
  const [expired, setExpired] = useState(() => Date.parse(proposal.expires_at) <= Date.now());
  useEffect(() => {
    const delay = Math.min(2_147_483_647, Math.max(0, Date.parse(proposal.expires_at) - Date.now()));
    const timer = window.setTimeout(() => setExpired(true), delay);
    return () => window.clearTimeout(timer);
  }, [proposal.expires_at]);
  function changeLine(index: number, changes: Partial<EditableLine>) {
    setLines((old) => old.map((line, i) => i === index ? { ...line, ...changes } : line)); setDirty(true);
  }
  function body(): DraftInput {
    const common = { warehouse_id: warehouse, reason };
    return sales ? { kind: "SALES", order: { ...common, customer_id: party,
      lines: lines.map((line) => ({ product_id: line.product_id, unit_id: line.unit_id, qty: line.qty,
        pricing_mode: line.pricing_mode,
        ...(line.pricing_mode === "MANUAL" ? { unit_price: line.unit_price } : {}) })) } }
      : { kind: "PURCHASE", order: { ...common, supplier_id: party,
        lines: lines.map((line) => ({ product_id: line.product_id, unit_id: line.unit_id,
          qty: line.qty, unit_price: line.unit_price })) } };
  }
  return <section aria-label="开单复核" className="w-full min-w-0">
    <ApprovalCard title={`${sales ? "销售" : "采购"}草稿复核`}
      description={`有效至 ${when(proposal.expires_at)}。修改后重新预览，再明确创建草稿。`}
      status={submission.busy ? "submitting" : "pending"} statusLabels={approvalStatusLabels}
      approveLabel="确认创建草稿" approveDisabled={locked || expired || dirty || mustPreview}
      onApprove={() => {
        if (locked || expired || dirty || mustPreview) return;
        const submitted = { expected_revision: revision, confirmation_hash: preview.confirmation_hash };
        void submission.submit((key) => protect(async () => {
          try { return await approveProposal(proposal.id, submitted, key); }
          catch (error) { if (error instanceof AssistantError && error.status === 409) setMustPreview(true); throw error; }
        }), async (created) => { onCreated(created); await onChanged(); });
      }}
      rejectLabel="取消此提案" onReject={locked || expired ? undefined : () => {
        void submission.submit((key) => protect(() => rejectProposal(proposal.id, revision, key)), onChanged);
      }}>
    <div className="space-y-5">
    {!allowed && <p role="alert">当前账户没有创建此类草稿的全部权限。</p>}
    {expired && <p role="alert">提案已过期，请重新提出开单请求。</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <div className="grid gap-4 sm:grid-cols-2">
      <EntitySelect resource={sales ? "customers" : "suppliers"} label={sales ? "客户" : "供应商"}
        value={party} currentName={preview.party_name} disabled={locked || expired}
        allowed={permissions.includes(sales ? "customer.read" : "supplier.read")}
        onChange={(value) => { setParty(value); setDirty(true); }} />
      <EntitySelect resource="warehouses" label="仓库" value={warehouse} currentName={preview.warehouse_name}
        disabled={locked || expired} allowed={permissions.includes("warehouse.read")}
        onChange={(value) => { setWarehouse(value); setDirty(true); }} />
    </div>
    <label className="block text-sm">开单说明<Input aria-label="开单说明" maxLength={2000} value={reason}
      disabled={locked || expired} onChange={(event) => { setReason(event.target.value); setDirty(true); }} /></label>
    <div className="space-y-3">{lines.map((line, index) => <div key={index} className="space-y-3 rounded-lg border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm font-medium">{index + 1}. {line.product_label} · {line.unit_label}</p>
        <div className="flex flex-wrap gap-2"><Button variant="ghost" size="sm" aria-label={`替换第 ${index + 1} 行商品或单位`}
          disabled={locked || expired || !permissions.includes("catalog.read")}
          onClick={() => setPicker(index)}>替换商品或单位</Button>
          <Button variant="ghost" size="sm" aria-label={`移除第 ${index + 1} 行`} disabled={locked || expired || lines.length <= 1}
            onClick={() => { setLines(lines.filter((_, i) => i !== index)); setDirty(true); }}>移除</Button></div></div>
      <div className="grid gap-3 sm:grid-cols-3"><label className="text-sm">数量<Input aria-label={`第 ${index + 1} 行数量`}
        inputMode="decimal" value={line.qty} disabled={locked || expired} onChange={(event) => changeLine(index, { qty: event.target.value })} /></label>
        {sales && <label className="text-sm">报价方式<select aria-label={`第 ${index + 1} 行报价方式`} value={line.pricing_mode}
          className={`${selectClass} mt-1 w-full`} disabled={locked || expired}
          onChange={(event) => changeLine(index, { pricing_mode: event.target.value as "AUTO" | "MANUAL" })}>
          <option value="AUTO">按当前报价规则</option><option value="MANUAL">手工复核单价</option></select></label>}
        <label className="text-sm">单价<Input aria-label={`第 ${index + 1} 行单价`} inputMode="decimal" value={line.unit_price}
          disabled={locked || expired || sales && line.pricing_mode === "AUTO"}
          onChange={(event) => changeLine(index, { unit_price: event.target.value })} /></label>
      </div>
    </div>)}</div>
    <Button variant="outline" disabled={locked || expired || lines.length >= 20 || !permissions.includes("catalog.read")}
      onClick={() => setPicker("add")}>添加商品</Button>
    {picker !== null && !locked && !expired && <div className="space-y-3 rounded-lg bg-muted/40 p-4">
      <Button variant="ghost" onClick={() => setPicker(null)}>收起选品</Button>
      <ProductPicker permissions={permissions} onSelect={({ product, snapshot, unitLabel }) => {
        if (lines.some((line, index) => line.product_id === product.id && index !== picker)) {
          setError("此商品已在草稿中，请修改原行数量。"); return;
        }
        const line: EditableLine = { product_id: product.id, unit_id: snapshot.unit_id,
          product_label: `${product.sku} · ${product.name}`, unit_label: unitLabel, qty: String(snapshot.qty),
          pricing_mode: sales ? "AUTO" : "MANUAL", unit_price: "" };
        setLines((old) => picker === "add" ? [...old, line] : old.map((item, index) => index === picker ? line : item));
        setPicker(null); setDirty(true); setError("");
      }} />
    </div>}
    {(dirty || mustPreview) ? <p role="status" className="text-sm text-amber-900 dark:text-amber-200">内容已变化，请重新预览金额和依据。</p>
      : <div className="space-y-3 rounded-2xl bg-muted/60 p-4"><p className="text-sm">服务器核算合计 <strong className="tabular-nums">{preview.total_amount}</strong> 元</p>
        <div className="overflow-auto"><table className="w-full text-left text-xs"><thead><tr>
          {['商品', '单位', '数量', '换算因子', '基本数量', '单价', '金额', '报价来源'].map((label) => <th className="p-2" key={label}>{label}</th>)}
        </tr></thead><tbody>{preview.lines.map((line) => <tr key={line.product_id}>
          <td className="p-2">{line.product_label}</td><td>{line.unit_label}</td><td>{line.qty}</td>
          <td>{line.unit_to_base_factor}</td><td>{line.base_qty}</td><td>{line.unit_price}</td><td>{line.amount}</td>
          <td>{priceSourceLabel(line.price_source.source)}</td>
        </tr>)}</tbody></table></div>
        {preview.warnings.map((warning, index) => <p className="text-xs" key={index}>{warning}</p>)}
      </div>}
    <div className="flex flex-wrap gap-3">
      <Button variant="outline" disabled={locked || expired || !reason.trim() || !lines.length} onClick={() => {
        const submitted = { expected_revision: revision, draft: body() };
        void submission.submit((key) => protect(() => editProposal(proposal.id, submitted, key)), async (next) => {
          if (next.preview) { setPreview(next.preview); setRevision(next.revision); setDirty(false); setMustPreview(false); }
          await onChanged();
        });
      }}>重新预览</Button>
    </div>
    </div>
    </ApprovalCard>
  </section>;
}
