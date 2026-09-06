"use client";

import type { ReactNode } from "react";
import {
  ArrowRight, ArrowUpRight, Boxes, ChartNoAxesCombined, ClipboardCheck,
  ClipboardList, Database, Package, PackageSearch, ShoppingBag, Truck,
  type LucideIcon,
} from "lucide-react";
import { ApprovalCard } from "@/components/agents/approval-card";
import { Button } from "@/components/ui/button";
import { safeSourceHref, type Evidence, type Proposal, type Turn } from "./assistant-client";

// Business compositions of the official beUI Chat App / Approval Card surfaces.
// Evidence is already server-authored. Never infer totals, availability or trends here.
const PREVIEW_ROWS = 3;
type Row = Evidence["rows"][number];
type ResultKind = "products" | "inventory" | "overview" | "replenishment" | "query";
const styles: Record<ResultKind, { label: string; icon: LucideIcon }> = {
  products: { label: "商品资料", icon: PackageSearch },
  inventory: { label: "库存记录", icon: Boxes },
  overview: { label: "经营概览", icon: ChartNoAxesCombined },
  replenishment: { label: "补货建议", icon: Truck },
  query: { label: "业务查询", icon: Database },
};
function resultKind(tool: string): ResultKind {
  if (["search_products", "get_product"].includes(tool)) return "products";
  if (["get_inventory", "get_low_stock_products"].includes(tool)) return "inventory";
  if (tool === "get_operating_overview") return "overview";
  if (tool === "get_replenishment_suggestions") return "replenishment";
  return "query";
}
function sourceTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}
function previewFacts(value: Evidence, kind: ResultKind) {
  const facts = value.summary.filter((fact) => !fact.label.includes("口径"));
  if (kind !== "overview") return facts.slice(0, 2);
  // Pick representative server metrics across the overview, not just its first group.
  const preferred = ["期间销售 · 销售净额", "期间收款 · 净收款", "期间付款 · 净付款", "当前应收 · 当前净余额", "当前应付 · 当前净余额", "当前库存 · 当前库存估值"];
  const selected = preferred.flatMap((label) => facts.filter((fact) => fact.label === label));
  return [...selected, ...facts.filter((fact) => !selected.includes(fact))].slice(0, 6);
}
function Value({ children, unit }: { children: ReactNode; unit?: string | null }) {
  return <span className="font-mono tabular-nums wrap-anywhere">{children}{unit ? <span className="ml-1 font-sans text-xs font-normal text-muted-foreground">{unit}</span> : null}</span>;
}
function RowFacts({ row, labels }: { row: Row; labels: string[] }) {
  return <dl className="grid min-w-0 grid-cols-2 gap-x-3 gap-y-2 text-xs">{labels.filter((label) => row[label] != null).map((label) =>
    <div className="min-w-0" key={label}><dt className="text-muted-foreground">{label}</dt><dd className="mt-1 leading-5"><Value>{row[label]}</Value></dd></div>)}</dl>;
}
function ProductRows({ rows }: { rows: Row[] }) {
  return <ul className="divide-y divide-border/60">{rows.map((row, index) => <li key={index} className="flex min-w-0 gap-3 py-3 first:pt-0 last:pb-0">
    <span className="grid size-10 shrink-0 place-items-center rounded-xl border border-border/60 bg-muted/40"><Package className="size-4 text-muted-foreground" aria-hidden /></span>
    <div className="min-w-0 flex-1"><p className="text-sm font-medium leading-5 wrap-anywhere">{row["名称"] ?? row["商品"] ?? "商品记录"}</p>
      {row["商品编码"] != null && <p className="mt-0.5 font-mono text-[11px] text-muted-foreground wrap-anywhere">{row["商品编码"]}</p>}
      {row["规格"] != null && <p className="mt-1 text-xs leading-5 text-muted-foreground wrap-anywhere">{row["规格"]}</p>}
      <div className="mt-2"><RowFacts row={row} labels={["标准价", "零售价", "批发价"]} /></div>
    </div>
  </li>)}</ul>;
}
function InventoryRows({ rows, lowStock }: { rows: Row[]; lowStock: boolean }) {
  return <ul className="space-y-2">{rows.map((row, index) => <li key={index} className="min-w-0 rounded-xl bg-muted/40 p-3">
    <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
      <div className="min-w-0"><p className="text-sm font-medium leading-5 wrap-anywhere">{row["商品"] ?? row["名称"] ?? row["商品编码"] ?? "库存记录"}</p>
        <p className="mt-1 text-[11px] text-muted-foreground wrap-anywhere">{[row["商品编码"], row["仓库"]].filter(Boolean).join(" · ")}</p></div>
      {row["可用数量"] != null && <div className="min-w-0"><p className="text-[11px] text-muted-foreground">可用数量</p><p className="mt-0.5 text-base font-medium"><Value unit={row["单位"]}>{row["可用数量"]}</Value></p></div>}
    </div>
    <div className="mt-3 border-t border-border/50 pt-2"><RowFacts row={row} labels={lowStock ? ["最低库存", "单位"] : ["现存数量", "占用数量"]} /></div>
  </li>)}</ul>;
}
function ReplenishmentRows({ rows }: { rows: Row[] }) {
  return <ul className="space-y-2">{rows.map((row, index) => <li key={index} className="min-w-0 rounded-xl border border-border/60 p-3">
    <p className="text-sm font-medium wrap-anywhere">{row["名称"] ?? row["商品"] ?? "补货商品"}</p>
    <p className="mt-1 text-[11px] text-muted-foreground wrap-anywhere">{[row["商品编码"], row["首选供应商"]].filter(Boolean).join(" · ")}</p>
    {row["建议采购基本数量"] != null && <div className="my-3 flex min-w-0 flex-wrap items-baseline justify-between gap-2 rounded-lg bg-muted/50 px-3 py-2"><span className="text-xs text-muted-foreground">建议采购基本数量</span><span className="text-lg font-medium"><Value unit={row["基本单位"]}>{row["建议采购基本数量"]}</Value></span></div>}
    <RowFacts row={row} labels={["可用数量", "采购在途"]} />
    {row["建议依据"] != null && <p className="mt-3 border-t border-border/50 pt-2 text-xs leading-5 text-muted-foreground wrap-anywhere">{row["建议依据"]}</p>}
  </li>)}</ul>;
}
function SummaryFacts({ facts, overview = false }: { facts: Evidence["summary"]; overview?: boolean }) {
  return <dl className={`grid min-w-0 gap-2 ${overview ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-2"}`}>{facts.map((fact, index) =>
    <div key={index} className={`min-w-0 ${overview ? "rounded-xl border border-border/60 bg-muted/20 p-3" : "py-1"}`}>
      <dt className="text-[11px] leading-5 text-muted-foreground wrap-anywhere">{fact.label}</dt>
      <dd className={`mt-1 whitespace-pre-wrap leading-6 ${overview ? "text-base font-medium" : "text-xs"}`}><Value unit={fact.unit}>{fact.value}</Value></dd>
    </div>)}</dl>;
}
function QueryRows({ value, rows }: { value: Evidence; rows: Row[] }) {
  return <ul className="space-y-2">{rows.map((row, index) => <li key={index} className="min-w-0 rounded-xl bg-muted/40 p-3"><RowFacts row={row} labels={value.columns.slice(0, 4)} /></li>)}</ul>;
}
function EvidenceResult({ value, onReview }: { value: Evidence; onReview: () => void }) {
  const kind = resultKind(value.tool);
  const { icon: Icon, label } = styles[kind];
  const rows = value.rows.slice(0, PREVIEW_ROWS);
  const summaryLimit = kind === "overview" ? 6 : 2;
  const facts = previewFacts(value, kind);
  const notices = value.summary.filter((fact) => fact.label.includes("口径"));
  const more = value.truncated || value.rows.length > PREVIEW_ROWS || value.summary.length > summaryLimit || (kind === "query" && value.columns.length > 4);
  const link = value.links.find((item) => safeSourceHref(item.href));
  return <section aria-label={`${value.title}结果摘要`} data-result-kind={kind} className="min-w-0 overflow-hidden rounded-2xl border border-border/70 bg-background text-sm">
    <header className="flex min-w-0 items-start gap-2.5 px-4 pt-4">
      <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-muted/70 text-muted-foreground"><Icon className="size-4" aria-hidden /></span>
      <div className="min-w-0 flex-1"><h3 className="text-sm font-medium leading-5 wrap-anywhere">{value.title}</h3><p className="mt-1 text-[11px] text-muted-foreground">{label}</p></div>
      <span className="shrink-0 rounded-full border border-border/70 px-2 py-0.5 text-[10px] text-muted-foreground">查询结果</span>
    </header>
    <div className="min-w-0 space-y-3 p-4">
      {!!facts.length && <SummaryFacts facts={facts} overview={kind === "overview"} />}
      {!!rows.length && (kind === "products" ? <ProductRows rows={rows} /> : kind === "inventory" ? <InventoryRows rows={rows} lowStock={value.tool === "get_low_stock_products"} /> : kind === "replenishment" ? <ReplenishmentRows rows={rows} /> : <QueryRows value={value} rows={rows} />)}
      {!rows.length && !facts.length && <p className="text-xs text-muted-foreground">本次查询未返回可展示的记录。</p>}
      <div className="space-y-1 text-[11px] leading-5 text-muted-foreground wrap-anywhere">
        <p>{value.scope}</p><p>查询时间 <time dateTime={value.as_of}>{sourceTime(value.as_of)}</time></p>
        {notices.map((fact, index) => <p key={index}>{fact.label}：{fact.value}{fact.unit ? ` ${fact.unit}` : ""}</p>)}
        {more && <p>这里展示部分摘要，完整数据与口径见业务依据。</p>}
      </div>
    </div>
    <footer className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-t border-border/50 bg-muted/20 px-3 py-2">
      <Button type="button" variant="ghost" size="xs" className="rounded-full" onClick={onReview}>查看业务依据<ArrowRight aria-hidden /></Button>
      {link && <a href={link.href} className="inline-flex min-h-8 min-w-0 items-center gap-1 rounded-full px-2 text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"><span className="min-w-0 wrap-anywhere">{link.label}</span><ArrowUpRight className="size-3 shrink-0" aria-hidden /></a>}
    </footer>
  </section>;
}
function ProposalResult({ proposal, onReview }: { proposal: Proposal; onReview: () => void }) {
  const sales = proposal.kind === "SALES";
  const kind = sales ? "销售" : "采购";
  const Icon = sales ? ShoppingBag : ClipboardList;
  if (proposal.receipt) return <section aria-label={`${kind}草稿创建回执`} data-result-kind="receipt" className="min-w-0">
    <ApprovalCard status="approved" title={`${kind}草稿已创建`} statusLabels={{ approved: "创建成功" }} className="rounded-2xl border border-border/70 bg-background p-4" result={<>
      <span className="block text-xs leading-6">已保存为草稿，可在业务页面继续复核。</span>
      <span className="mt-2 flex flex-wrap items-center gap-2 text-xs"><ClipboardCheck className="size-3.5" aria-hidden />状态：草稿</span>
      <Button type="button" size="xs" variant="ghost" className="mt-2 rounded-full" onClick={onReview}>查看草稿结果<ArrowRight aria-hidden /></Button>
      {safeSourceHref(proposal.receipt.href) && <a className="mt-3 inline-flex min-h-8 items-center gap-1 rounded-full text-xs text-foreground underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring" href={proposal.receipt.href}>查看{kind}草稿<ArrowUpRight className="size-3" aria-hidden /></a>}
    </>} />
  </section>;
  const pending = proposal.status === "PENDING" && !!proposal.preview;
  if (!pending) return <section aria-label={`${kind}草稿提案状态`} data-result-kind="proposal-status" className="min-w-0 rounded-2xl border border-border/70 bg-muted/20 p-4">
    <p className="flex items-center gap-2 text-sm font-medium"><Icon className="size-4 text-muted-foreground" aria-hidden />{kind}草稿提案</p>
    <p className="mt-2 text-xs leading-6 text-muted-foreground">{proposal.status === "REJECTED" ? "此提案已取消。" : proposal.status === "EXPIRED" ? "此提案已过期，请重新提出开单请求。" : "此提案暂时不可复核，请查看详细状态。"}</p>
    <Button type="button" size="xs" variant="ghost" className="mt-2 rounded-full" onClick={onReview}>查看草稿结果<ArrowRight aria-hidden /></Button>
  </section>;
  const preview = proposal.preview!;
  return <section aria-label={`${kind}草稿预览摘要`} data-result-kind={sales ? "sales-draft" : "purchase-draft"} className="min-w-0">
    <ApprovalCard title={<span className="inline-flex items-center gap-2"><Icon className="size-4" aria-hidden />{kind}草稿预览</span>}
      description="请先复核商品、数量与金额，再明确创建草稿。" status="pending" statusLabels={{ pending: "待你复核" }} approveLabel="复核草稿" onApprove={onReview}
      className="rounded-2xl border border-border/70 bg-background p-4">
      <dl className="grid min-w-0 grid-cols-2 gap-3 text-xs"><div className="min-w-0"><dt className="text-muted-foreground">{sales ? "客户" : "供应商"}</dt><dd className="mt-1 font-medium leading-5 wrap-anywhere">{preview.party_name}</dd></div><div className="min-w-0"><dt className="text-muted-foreground">仓库</dt><dd className="mt-1 leading-5 wrap-anywhere">{preview.warehouse_name}</dd></div></dl>
      <ul className="mt-3 divide-y divide-border/60 rounded-xl bg-muted/40 px-3">{preview.lines.slice(0, PREVIEW_ROWS).map((line, index) => <li key={`${line.product_id}-${index}`} className="flex min-w-0 flex-wrap items-start justify-between gap-2 py-2.5 text-xs"><div className="min-w-0 flex-1"><p className="font-medium leading-5 wrap-anywhere">{line.product_label}</p><p className="mt-1 text-muted-foreground"><Value unit={line.unit_label}>{line.qty}</Value></p></div><p className="min-w-0 py-0.5"><Value unit="元">{line.amount}</Value></p></li>)}</ul>
      {preview.lines.length > PREVIEW_ROWS && <p className="mt-2 text-[11px] text-muted-foreground">更多商品见草稿复核。</p>}
      <div className="mt-3 flex min-w-0 flex-wrap items-baseline justify-between gap-2 border-t border-border/60 pt-3"><span className="text-xs text-muted-foreground">服务器核算合计</span><strong className="text-lg"><Value unit="元">{preview.total_amount}</Value></strong></div>
      {preview.warnings.map((warning, index) => <p key={index} className="mt-2 text-[11px] leading-5 text-muted-foreground wrap-anywhere">{warning}</p>)}
    </ApprovalCard>
  </section>;
}

/** Thread previews open the existing mounted detail panel; they never mutate business data. */
export function BusinessResultCards({ turn, onReview }: { turn: Turn; onReview: () => void }) {
  if (!turn.evidence?.length && !turn.proposal) return null;
  return <div className="mt-3 grid w-full min-w-0 gap-3" aria-label="本轮业务结果">
    {(turn.evidence ?? []).map((value, index) => <EvidenceResult key={`${value.id}-${index}`} value={value} onReview={onReview} />)}
    {turn.proposal && <ProposalResult proposal={turn.proposal} onReview={onReview} />}
  </div>;
}
