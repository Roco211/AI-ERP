import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { BusinessResultCards } from "@/features/ai/business-result-cards";
import type { Evidence, Proposal, Turn } from "@/features/ai/assistant-client";

const documentId = "20000000-0000-4000-8000-000000000002";
function evidence(overrides: Partial<Evidence> = {}): Evidence {
  return {
    id: "evidence-1", tool: "get_inventory", title: "库存余额", as_of: "2026-09-06T10:00:00Z",
    scope: "当前库存；各次工具查询时点独立。", summary: [], columns: ["商品", "仓库", "单位", "可用数量"],
    rows: [{ 商品: "精密螺栓", 仓库: "主仓", 单位: "个", 可用数量: "9007199254740993.000001", 现存数量: "9007199254740994.000001", 占用数量: "1.000000" }],
    links: [{ label: "打开库存", href: "/inventory" }], truncated: false, ...overrides,
  };
}
function turn(overrides: Partial<Turn> = {}): Turn {
  return { id: "turn-1", prompt: "查询库存", state: "COMPLETED", answer: "已查询", created_at: "2026-09-06T10:00:00Z", evidence: [], can_retry: false, model_calls: 1, tool_calls: 1, guided: false, interaction: "business", ...overrides };
}
function proposal(kind: "SALES" | "PURCHASE" = "SALES"): Proposal {
  return {
    id: "proposal-1", turn_id: "turn-1", kind, status: "PENDING", revision: 1, expires_at: "2099-09-06T10:00:00Z",
    preview: { kind, party_name: kind === "SALES" ? "宏达客户" : "恒丰供应商", warehouse_name: "主仓", total_amount: "9007199254740993.0000", confirmation_hash: "a".repeat(64), warnings: ["请核对单价来源。"],
      order: { warehouse_id: "warehouse-1", reason: "客户需求", lines: [], ...(kind === "SALES" ? { customer_id: "customer-1" } : { supplier_id: "supplier-1" }) },
      lines: [{ product_id: "product-1", product_label: "M8 · 精密螺栓", unit_id: "unit-1", unit_label: "个", qty: "1.000001", base_qty: "1.000001", unit_price: "9007199254740993.000000", amount: "9007199254740993.0000", unit_to_base_factor: "1.000000", conversion_version: 1, pricing_mode: "MANUAL", price_source: { source: "manual" } }],
    },
  };
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("business results are absent for casual answers without facts or drafts", () => {
  const { container } = render(<BusinessResultCards turn={turn()} onReview={vi.fn()} />);
  expect(container).toBeEmptyDOMElement();
});

test("inventory cards preserve server decimals, source scope and time without computing new amounts", () => {
  render(<BusinessResultCards turn={turn({ evidence: [evidence()] })} onReview={vi.fn()} />);
  const card = screen.getByRole("region", { name: "库存余额结果摘要" });
  expect(card).toHaveAttribute("data-result-kind", "inventory");
  expect(card).toHaveTextContent("9007199254740993.000001");
  expect(card).toHaveTextContent("9007199254740994.000001");
  expect(card).toHaveTextContent("1.000000");
  expect(card).toHaveTextContent("各次工具查询时点独立");
  expect(card.querySelector("time")).toHaveAttribute("datetime", "2026-09-06T10:00:00Z");
  expect(within(card).getByRole("link", { name: "打开库存" })).toHaveAttribute("href", "/inventory");
});

test("product cards escape business text, retain quoted prices, bound rows and open full evidence", () => {
  const onReview = vi.fn();
  const rows = Array.from({ length: 4 }, (_, i) => ({ 商品编码: `M${i}`, 名称: i === 0 ? "螺栓 <script>bad()</script>" : `商品${i}`, 规格: "M8 × 30", 标准价: "0.123456" }));
  render(<BusinessResultCards turn={turn({ evidence: [evidence({ tool: "search_products", title: "商品检索", columns: ["商品编码", "名称", "规格", "标准价"], rows, links: [] })] })} onReview={onReview} />);
  const card = screen.getByRole("region", { name: "商品检索结果摘要" });
  expect(card).toHaveAttribute("data-result-kind", "products");
  expect(card).toHaveTextContent("螺栓 <script>bad()</script>");
  expect(card.querySelector("script")).toBeNull();
  expect(card).toHaveTextContent("0.123456");
  expect(card).not.toHaveTextContent("商品3");
  expect(card).toHaveTextContent("部分摘要");
  const review = within(card).getByRole("button", { name: "查看业务依据" });
  review.focus();
  expect(review).toHaveFocus();
  fireEvent.click(review);
  expect(onReview).toHaveBeenCalledOnce();
});

test("overview and replenishment have distinct layouts with original metric and quantity labels", () => {
  render(<BusinessResultCards turn={turn({ evidence: [
    evidence({ id: "overview", tool: "get_operating_overview", title: "经营概览", scope: "期间：2026-09-01 至 2026-09-06；应收应付和库存为当前余额，不是历史期末。", rows: [], columns: [], summary: [{ label: "期间销售 · 销售净额", value: "123456789.1234", unit: "元" }, { label: "当前库存 · 当前库存估值", value: "1000000.9999", unit: "元" }] }),
    evidence({ id: "replenishment", tool: "get_replenishment_suggestions", title: "补货建议", rows: [{ 名称: "螺母", 商品编码: "M8", 首选供应商: "恒丰供应商", 建议采购基本数量: "3.000001", 基本单位: "个", 可用数量: "0.100000", 采购在途: "0.000000", 建议依据: "达到补货触发条件且存在缺口" }] }),
  ] })} onReview={vi.fn()} />);
  const overview = screen.getByRole("region", { name: "经营概览结果摘要" });
  expect(overview).toHaveAttribute("data-result-kind", "overview");
  expect(overview).toHaveTextContent("期间销售 · 销售净额");
  expect(overview).toHaveTextContent("123456789.1234");
  expect(overview).toHaveTextContent("不是历史期末");
  const replenish = screen.getByRole("region", { name: "补货建议结果摘要" });
  expect(replenish).toHaveAttribute("data-result-kind", "replenishment");
  expect(replenish).toHaveTextContent("建议采购基本数量3.000001");
  expect(replenish).toHaveTextContent("恒丰供应商");
  expect(replenish).toHaveTextContent("达到补货触发条件且存在缺口");
  expect(screen.queryByRole("button", { name: /创建|采购下单/ })).not.toBeInTheDocument();
});

test("unfamiliar evidence stays inspectable with a bounded generic summary and no invented data", () => {
  render(<BusinessResultCards turn={turn({ evidence: [evidence({ tool: "get_future_query", title: "查询明细", rows: [], summary: [{ label: "查询结果", value: "当前筛选没有记录" }] })] })} onReview={vi.fn()} />);
  const card = screen.getByRole("region", { name: "查询明细结果摘要" });
  expect(card).toHaveAttribute("data-result-kind", "query");
  expect(card).toHaveTextContent("当前筛选没有记录");
  expect(card).not.toHaveTextContent("9007199254740993");
  expect(within(card).getByRole("button", { name: "查看业务依据" })).toBeEnabled();
});

test("overview previews span business groups and always retain the server's statistical caveat", () => {
  render(<BusinessResultCards turn={turn({ evidence: [evidence({ tool: "get_operating_overview", title: "经营概览", rows: [], columns: [], summary: [
    ...Array.from({ length: 7 }, (_, i) => ({ label: `期间销售 · 明细${i}`, value: String(i) })),
    { label: "期间销售 · 销售净额", value: "1000.1234", unit: "元" },
    { label: "当前库存 · 当前库存估值", value: "9999.9999", unit: "元" },
    { label: "统计口径", value: "按当前有效事实重述，不代表历史期末。" },
  ] })] })} onReview={vi.fn()} />);
  const card = screen.getByRole("region", { name: "经营概览结果摘要" });
  expect(card).toHaveTextContent("1000.1234");
  expect(card).toHaveTextContent("9999.9999");
  expect(card).toHaveTextContent("统计口径：按当前有效事实重述，不代表历史期末。");
  expect(card).toHaveTextContent("部分摘要");
});

test.each(["SALES", "PURCHASE"] as const)("%s draft summary opens existing review only and has no duplicate editor", (kind) => {
  const onReview = vi.fn();
  render(<BusinessResultCards turn={turn({ proposal: proposal(kind) })} onReview={onReview} />);
  const card = screen.getByRole("region", { name: `${kind === "SALES" ? "销售" : "采购"}草稿预览摘要` });
  expect(card).toHaveAttribute("data-result-kind", kind === "SALES" ? "sales-draft" : "purchase-draft");
  expect(card).toHaveTextContent(kind === "SALES" ? "宏达客户" : "恒丰供应商");
  expect(card).toHaveTextContent("9007199254740993.0000");
  expect(card).toHaveTextContent("1.000001");
  expect(card).toHaveTextContent("待你复核");
  expect(card).toHaveTextContent("请核对单价来源");
  expect(within(card).queryByRole("textbox")).not.toBeInTheDocument();
  expect(within(card).queryByRole("button", { name: "确认创建草稿" })).not.toBeInTheDocument();
  fireEvent.click(within(card).getByRole("button", { name: "复核草稿" }));
  expect(onReview).toHaveBeenCalledOnce();
});

test.each(["REJECTED", "EXPIRED"] as const)("%s proposal does not render actionable preview amounts", (status) => {
  render(<BusinessResultCards turn={turn({ proposal: { ...proposal(), status } })} onReview={vi.fn()} />);
  const card = screen.getByRole("region", { name: "销售草稿提案状态" });
  expect(card).toHaveTextContent(status === "REJECTED" ? "此提案已取消" : "此提案已过期");
  expect(card).not.toHaveTextContent("9007199254740993.0000");
  expect(within(card).queryByRole("button", { name: "复核草稿" })).not.toBeInTheDocument();
});

test("creation receipt links only to an approved server business path and does not reuse stale preview amounts", () => {
  const receipt = { proposal_id: "proposal-1", id: documentId, request_id: "request-1", href: `/sales?order=${documentId}`, status: "DRAFT" as const, version: 1 };
  const view = render(<BusinessResultCards turn={turn({ proposal: { ...proposal(), status: "CREATED", receipt } })} onReview={vi.fn()} />);
  const card = screen.getByRole("region", { name: "销售草稿创建回执" });
  expect(card).toHaveTextContent("状态：草稿");
  expect(card).not.toHaveTextContent("9007199254740993.0000");
  expect(within(card).getByRole("link", { name: "查看销售草稿" })).toHaveAttribute("href", receipt.href);
  view.rerender(<BusinessResultCards turn={turn({ proposal: { ...proposal(), status: "CREATED", receipt: { ...receipt, href: "javascript:alert(1)" } } })} onReview={vi.fn()} />);
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});

test("invalid source URLs never become links and do not hide a later valid source", () => {
  render(<BusinessResultCards turn={turn({ evidence: [evidence({ links: [
    { label: "恶意脚本", href: "javascript:alert(1)" }, { label: "外部站点", href: "//evil.example" },
    { label: "重复参数", href: `/sales?order=${documentId}&order=${documentId}` },
    { label: "合法来源", href: `/sales?order=${documentId}` },
  ] })] })} onReview={vi.fn()} />);
  expect(screen.getAllByRole("link")).toHaveLength(1);
  expect(screen.getByRole("link", { name: "合法来源" })).toHaveAttribute("href", `/sales?order=${documentId}`);
});
