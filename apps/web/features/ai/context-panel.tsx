"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, ChevronDown, Database } from "lucide-react";
import { ApprovalCard } from "@/components/agents/approval-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ProductPicker } from "@/features/catalog/picker";
import { resourceClients, unwrap } from "@/features/catalog/client";
import { selectClass } from "@/features/operations/presentation";
import type { useOperationSubmission } from "@/features/operations/use-submission";
import {
  AssistantError, editProposal, approveProposal, rejectProposal, safeSourceHref,
  type Evidence, type Proposal, type Preview, type Receipt, type DraftInput,
} from "./assistant-client";

type Submission = ReturnType<typeof useOperationSubmission>;
const hasAll = (permissions: string[], required: string[]) => required.every((p) => permissions.includes(p));
const when = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });
const PRICE_SOURCE_LABELS: Record<string, string> = {
  standard: "标准价", customer: "客户专价", history: "有效成交历史",
  retail: "零售价", wholesale: "批发价", manual: "手工复核",
};
const priceSourceLabel = (value: unknown) => PRICE_SOURCE_LABELS[String(value)] ?? "当前报价规则";
const approvalStatusLabels = { pending: "等待你复核", submitting: "正在提交", approved: "已创建草稿" };

export function EvidenceCard({ value }: { value: Evidence }) {
  return <section aria-label={value.title} className="w-full min-w-0 space-y-4 rounded-2xl border border-border/70 bg-background p-4">
    <h3 className="flex items-start gap-2 text-sm font-medium leading-6 wrap-anywhere"><Database className="mt-1 size-4 shrink-0 text-muted-foreground" aria-hidden />{value.title}</h3>
    <p className="text-xs leading-5 text-muted-foreground wrap-anywhere">查询时间：{when(value.as_of)} · {value.scope}</p>
    <dl className="grid min-w-0 gap-3 text-sm">{value.summary.map((fact, index) =>
      <div key={index} className="min-w-0"><dt className="text-xs text-muted-foreground">{fact.label}</dt>
        <dd className="mt-1 whitespace-pre-wrap font-mono leading-6 tabular-nums wrap-anywhere">{fact.value}{fact.unit ? ` ${fact.unit}` : ""}</dd></div>)}</dl>
    {!!value.columns.length && <div className="max-w-full overflow-auto rounded-xl border border-border/70"><table className="w-full text-left text-sm">
      <thead><tr>{value.columns.map((column) => <th key={column} className="whitespace-nowrap border-b p-2 font-medium">{column}</th>)}</tr></thead>
      <tbody>{value.rows.map((row, index) => <tr key={index}>{value.columns.map((column) =>
        <td key={column} className="max-w-xs whitespace-pre-wrap break-words border-b p-2 tabular-nums">{row[column] ?? "—"}</td>)}</tr>)}</tbody>
    </table></div>}
    {value.truncated && <p className="text-xs text-muted-foreground">当前只展示部分记录，可补充条件缩小范围。</p>}
    <div className="flex flex-wrap gap-3 text-sm">{value.links.filter((link) => safeSourceHref(link.href)).map((link) =>
      <a className="inline-flex min-w-0 items-start gap-1 text-foreground underline decoration-border underline-offset-4 wrap-anywhere" key={link.href} href={link.href}>{link.label}<ArrowUpRight className="size-3.5" aria-hidden /></a>)}</div>
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
  return <div className="min-w-0 space-y-2"><label className="block min-w-0 text-sm">{label}
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

/** Keep this card mounted while its parent context panel is hidden. */
export function ProposalCard({ proposal, permissions, submission, blocked, protect, onChanged }: {
  proposal: Proposal; permissions: string[]; submission: Submission; blocked: boolean;
  protect: <T>(operation: () => Promise<T>) => Promise<T>; onChanged: () => Promise<void>;
}) {
  const [receipt, setReceipt] = useState<Receipt | null>(proposal.receipt ?? null);
  if (receipt) return <section aria-label="草稿创建结果" className="w-full min-w-0">
    <ApprovalCard className="border border-border/70 bg-background p-3" status="approved" statusLabels={approvalStatusLabels} title="草稿已创建" result={<>
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
    <ApprovalCard className="border border-border/70 bg-background p-3" title={`${sales ? "销售" : "采购"}草稿复核`}
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
    <div className="grid min-w-0 gap-4">
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
    <div className="space-y-3">{lines.map((line, index) => <div key={index} className="min-w-0 space-y-3 rounded-2xl border border-border/70 bg-muted/20 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><p className="min-w-0 text-sm font-medium leading-6 wrap-anywhere">{index + 1}. {line.product_label} · {line.unit_label}</p>
        <div className="flex flex-wrap gap-2"><Button variant="ghost" size="sm" aria-label={`替换第 ${index + 1} 行商品或单位`}
          disabled={locked || expired || !permissions.includes("catalog.read")}
          onClick={() => setPicker(index)}>替换商品或单位</Button>
          <Button variant="ghost" size="sm" aria-label={`移除第 ${index + 1} 行`} disabled={locked || expired || lines.length <= 1}
            onClick={() => { setLines(lines.filter((_, i) => i !== index)); setDirty(true); }}>移除</Button></div></div>
      {sales && <label className="block min-w-0 text-sm">报价方式<select aria-label={`第 ${index + 1} 行报价方式`} value={line.pricing_mode}
        className={`${selectClass} mt-1 w-full`} disabled={locked || expired}
        onChange={(event) => changeLine(index, { pricing_mode: event.target.value as "AUTO" | "MANUAL" })}>
        <option value="AUTO">按当前报价规则</option><option value="MANUAL">手工复核单价</option></select></label>}
      <div className="grid min-w-0 grid-cols-2 gap-3">
        <label className="min-w-0 text-sm">数量<Input aria-label={`第 ${index + 1} 行数量`}
          inputMode="decimal" value={line.qty} disabled={locked || expired} onChange={(event) => changeLine(index, { qty: event.target.value })} /></label>
        <label className="min-w-0 text-sm">单价<Input aria-label={`第 ${index + 1} 行单价`} inputMode="decimal" value={line.unit_price}
          disabled={locked || expired || sales && line.pricing_mode === "AUTO"}
          onChange={(event) => changeLine(index, { unit_price: event.target.value })} /></label>
      </div>
    </div>)}</div>
    <Button variant="outline" disabled={locked || expired || lines.length >= 20 || !permissions.includes("catalog.read")}
      onClick={() => setPicker("add")}>添加商品</Button>
    {picker !== null && !locked && !expired && <div className="min-w-0 space-y-3 rounded-2xl bg-muted/40 p-3 wrap-anywhere [&>div>div.grid]:grid-cols-1">
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
      : <div className="min-w-0 space-y-3 rounded-2xl bg-muted/60 p-3">
        <p className="text-sm leading-6">服务器核算合计 <strong className="font-mono tabular-nums wrap-anywhere">{preview.total_amount}</strong> 元</p>
        <p className="text-xs leading-5 text-muted-foreground">展开商品查看数量、换算和报价依据。</p>
        <div className="space-y-2">{preview.lines.map((line) => <details key={line.product_id} className="group min-w-0 rounded-xl border border-border/70 bg-background/70">
          <summary className="flex cursor-pointer list-none items-start gap-2 rounded-xl p-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
            <span className="min-w-0 flex-1 leading-6 wrap-anywhere">{line.product_label}
              <span className="block font-mono text-xs text-muted-foreground">金额 {line.amount} 元</span>
            </span>
            <ChevronDown aria-hidden className="mt-1 size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180 motion-reduce:transition-none" />
          </summary>
          <dl className="grid min-w-0 grid-cols-2 gap-x-3 gap-y-4 border-t border-border/70 p-3 text-xs">
            {[
              ["商品", line.product_label], ["单位", line.unit_label], ["数量", line.qty],
              ["换算因子", line.unit_to_base_factor], ["基本数量", line.base_qty],
              ["单价", line.unit_price], ["金额", line.amount], ["报价来源", priceSourceLabel(line.price_source.source)],
            ].map(([label, value]) => <div key={label} className={`min-w-0 ${label === "商品" ? "col-span-2" : ""}`}>
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="mt-1 whitespace-pre-wrap font-mono leading-5 tabular-nums wrap-anywhere">{value}</dd>
            </div>)}
          </dl>
        </details>)}</div>
        {preview.warnings.map((warning, index) => <p className="text-xs leading-5 wrap-anywhere" key={index}>{warning}</p>)}
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
