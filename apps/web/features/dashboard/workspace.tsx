"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Dialog } from "@base-ui/react/dialog";
import { z } from "zod";
import { api } from "@/lib/api";
import { unwrap } from "@/features/inventory/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Facts, OperationsTable } from "@/features/operations/presentation";
import {
  hasEvery,
  replenishmentReadPermissions,
} from "@/features/operations/permissions";
import type { components } from "@/generated/api/schema";

type Metric = components["schemas"]["ReportSources"]["metric"];
type Source = components["schemas"]["ReportSource"];
type Integration = "ACTIVE" | "INCOMPLETE" | "NOT_ENABLED";
const metricNames: Record<Metric, string> = {
  sales: "期间销售来源",
  cash_ar: "期间收款与客户退款",
  cash_ap: "期间付款与供应商退款",
  current_ar: "当前应收来源",
  current_ap: "当前应付来源",
  inventory: "当前库存来源",
  low_stock: "当前低库存商品",
  replenishment: "当前补货候选",
};
const date = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
function IntegrationNotice({
  status,
  count,
}: {
  status?: Integration | null;
  count?: number | null;
}) {
  if (status === "NOT_ENABLED")
    return <p className="text-sm">资金管理尚未启用，金额暂不可用。</p>;
  if (status === "INCOMPLETE")
    return (
      <p className="text-sm">
        还有 {count}{" "}
        张历史单据未绑定期初。当前仅展示已纳入的来源，不代表完整历史往来。
      </p>
    );
  return null;
}

export function DashboardWorkspace({ permissions }: { permissions: string[] }) {
  const has = (permission: string) => permissions.includes(permission);
  const read = has("dashboard.read");
  const sale = read && has("sales.read");
  const price = sale && has("product.price.read");
  const margin = price && has("product.cost.read");
  const inventory = read && has("inventory.read");
  const cost = inventory && has("product.cost.read");
  const ar = read && has("funds.ar.read"),
    ap = read && has("funds.ap.read");
  const replenishment =
    read && hasEvery(permissions, replenishmentReadPermissions);
  const canMetric = (metric: Metric) =>
    metric === "sales"
      ? sale
      : ["cash_ar", "current_ar"].includes(metric)
        ? ar
        : ["cash_ap", "current_ap"].includes(metric)
          ? ap
          : metric === "replenishment"
            ? replenishment
            : inventory;
  const [range, setRange] = useState<{ date_from?: string; date_to?: string }>(
    {},
  );
  const [draftFrom, setDraftFrom] = useState<string | null>(null);
  const [draftTo, setDraftTo] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [metric, setMetric] = useState<Metric | null>(null);
  const [page, setPage] = useState(1);
  const permissionKey = permissions.join("|");
  const overview = useQuery({
    queryKey: ["reporting", "overview", range, permissionKey],
    enabled: read,
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/reporting/overview", {
          signal,
          params: { query: range },
        }),
      ),
  });
  const sourceRange = {
    date_from: overview.data?.date_from ?? range.date_from,
    date_to: overview.data?.date_to ?? range.date_to,
  };
  const sources = useQuery({
    queryKey: [
      "reporting",
      "sources",
      metric,
      sourceRange,
      page,
      permissionKey,
    ],
    enabled: !!metric && !!overview.data && canMetric(metric),
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/reporting/sources", {
          signal,
          params: {
            query: { ...sourceRange, metric: metric!, page, page_size: 25 },
          },
        }),
      ),
  });
  const value = read ? overview.data : undefined;
  const detail =
    metric && canMetric(metric) && sources.data?.metric === metric
      ? sources.data
      : undefined;
  function openSources(next: Metric) {
    setMetric(next);
    setPage(1);
  }
  function card(title: string, sourceMetric: Metric, body: ReactNode) {
    return (
      <section
        aria-label={title}
        className="min-w-0 space-y-4 rounded-xl border bg-white p-4 sm:p-6"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-medium">{title}</h3>
          <Button variant="outline" onClick={() => openSources(sourceMetric)}>
            查看来源
          </Button>
        </div>
        {body}
      </section>
    );
  }
  function sourceLink(row: Source) {
    if (metric === "sales" && row.document_id)
      return (
        <Link
          className="text-primary underline"
          href={`/sales?document=${encodeURIComponent(row.document_id)}`}
        >
          查看销售单据
        </Link>
      );
    if (metric === "cash_ar" || metric === "cash_ap")
      return (
        <Link
          className="text-primary underline"
          href={`/funds?side=${metric === "cash_ar" ? "AR" : "AP"}&cash=${encodeURIComponent(row.id)}`}
        >
          查看收付款
        </Link>
      );
    if (metric === "current_ar" || metric === "current_ap")
      return (
        <Link
          className="text-primary underline"
          href={`/funds?side=${metric === "current_ar" ? "AR" : "AP"}&source=${encodeURIComponent(row.id)}`}
        >
          查看资金来源
        </Link>
      );
    return "—";
  }
  function sourceValues(row: Source): [string, ReactNode][] {
    if (metric === "sales")
      return [
        ["日期", row.date],
        ["数量", row.qty],
        ["单位", row.unit_name],
        ...(price
          ? ([
              ["原单金额", row.amount],
              ["本期增减", row.signed_amount],
            ] as [string, ReactNode][])
          : []),
        ...(margin
          ? ([
              [
                "实际成本增减",
                row.cost_status === "AVAILABLE" ? row.signed_cost : null,
              ],
              [
                "实际毛利",
                row.cost_status === "AVAILABLE" ? row.gross_margin : null,
              ],
              [
                "成本状态",
                row.cost_status === "AVAILABLE" ? "可用" : "成本事实缺失",
              ],
            ] as [string, ReactNode][])
          : []),
      ];
    if (metric === "cash_ar" || metric === "cash_ap")
      return [
        ["业务日期", row.date],
        ["实际金额", row.amount],
        [metric === "cash_ar" ? "净流入增减" : "净流出增减", row.signed_amount],
      ];
    if (metric === "current_ar" || metric === "current_ap")
      return [
        ["待结算", row.settlement_amount],
        ["待退款", row.refund_amount],
        ["往来净额", row.balance],
      ];
    if (metric === "replenishment")
      return [
        ["当前可用量", row.available_qty],
        ["安全库存", row.min_stock_qty],
        ["建议基础量", row.suggested_base_qty],
        ["基础单位", row.unit_name],
      ];
    return [
      ["仓库", row.warehouse_name],
      ["现有量", row.qty],
      ["占用量", row.reserved_qty],
      ["可用量", row.available_qty],
      ["基础单位", row.unit_name],
      ...(metric === "low_stock"
        ? ([["最低库存", row.min_stock_qty]] as [string, ReactNode][])
        : []),
      ...(cost && metric === "inventory"
        ? ([["当前库存估值", row.valuation]] as [string, ReactNode][])
        : []),
    ];
  }
  return (
    <section aria-label="经营概览" className="mt-8 min-w-0 space-y-6">
      {!read ? (
        <p role="alert">
          当前没有查看经营概览的权限。可通过导航进入已获授权的业务页面。
        </p>
      ) : (
        <>
          <form
            className="flex min-w-0 flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              const from = draftFrom ?? value?.date_from ?? "",
                to = draftTo ?? value?.date_to ?? "";
              if (
                !date.safeParse(from).success ||
                !date.safeParse(to).success ||
                from > to
              ) {
                setError("请选择有效的起止日期，起始日期不能晚于结束日期。");
                return;
              }
              setError("");
              setRange({ date_from: from, date_to: to });
              setMetric(null);
              setPage(1);
            }}
          >
            <label className="min-w-0 text-sm">
              开始日期
              <Input
                type="date"
                aria-label="概览开始日期"
                value={draftFrom ?? value?.date_from ?? ""}
                onChange={(event) => setDraftFrom(event.target.value)}
                className="mt-1"
              />
            </label>
            <label className="min-w-0 text-sm">
              结束日期
              <Input
                type="date"
                aria-label="概览结束日期"
                value={draftTo ?? value?.date_to ?? ""}
                onChange={(event) => setDraftTo(event.target.value)}
                className="mt-1"
              />
            </label>
            <Button type="submit">更新概览</Button>
            <Button
              variant="outline"
              type="button"
              onClick={() => void overview.refetch()}
            >
              刷新概览
            </Button>
          </form>
          {error && <p role="alert">{error}</p>}
          {overview.isPending && <p role="status">正在读取经营概览…</p>}
          {overview.error && <p role="alert">{overview.error.message}</p>}
          {value && !overview.error && (
            <>
              <p className="text-sm text-muted-foreground">
                业务时区 {value.business_timezone} · 统计日期 {value.date_from}{" "}
                至 {value.date_to} · 当前数据时间 {value.as_of}
              </p>
              <p className="text-sm text-muted-foreground">
                {value.restatement_notice}
              </p>
              <section aria-label="期间经营数据" className="space-y-4">
                <h2 className="text-lg font-medium">所选期间</h2>
                <div className="grid min-w-0 gap-4 xl:grid-cols-2">
                  {sale &&
                    value.sales &&
                    card(
                      "期间销售",
                      "sales",
                      <>
                        <Facts
                          values={[
                            ["已过账出库单数", value.sales.shipment_count],
                            ["已过账退货单数", value.sales.return_count],
                            ...(price
                              ? ([
                                  ["出库销售额", value.sales.shipment_amount],
                                  ["退货冲减", value.sales.return_amount],
                                  ["净销售额", value.sales.net_sales_amount],
                                ] as [string, ReactNode][])
                              : []),
                            ...(margin
                              ? ([
                                  [
                                    "实际净成本",
                                    value.sales.cost_status === "AVAILABLE"
                                      ? value.sales.net_cost
                                      : null,
                                  ],
                                  [
                                    "已实现净毛利",
                                    value.sales.cost_status === "AVAILABLE"
                                      ? value.sales.gross_margin
                                      : null,
                                  ],
                                ] as [string, ReactNode][])
                              : []),
                          ]}
                        />
                        {margin &&
                          value.sales.cost_status === "MISSING_FACTS" && (
                            <p>存在缺失的实际成本事实，成本与毛利暂不可用。</p>
                          )}
                        <details>
                          <summary className="cursor-pointer text-sm text-primary">
                            查看每日销售变化
                          </summary>
                          <div className="mt-3">
                            <OperationsTable
                              headers={[
                                "日期",
                                "出库单数",
                                "退货单数",
                                ...(price ? ["净销售额"] : []),
                                ...(margin ? ["实际净成本", "实际毛利"] : []),
                              ]}
                              rows={value.sales.daily.map((day) => ({
                                id: day.date,
                                cells: [
                                  day.date,
                                  day.shipment_count,
                                  day.return_count,
                                  ...(price
                                    ? [day.net_sales_amount ?? "—"]
                                    : []),
                                  ...(margin
                                    ? [
                                        day.cost_status === "AVAILABLE"
                                          ? (day.net_cost ?? "—")
                                          : "—",
                                        day.cost_status === "AVAILABLE"
                                          ? (day.gross_margin ?? "—")
                                          : "—",
                                      ]
                                    : []),
                                ],
                              }))}
                            />
                          </div>
                        </details>
                      </>,
                    )}
                  {(["ar", "ap"] as const).map((side) => {
                    const cash = side === "ar" ? value.cash_ar : value.cash_ap;
                    if (!(side === "ar" ? ar : ap) || !cash) return null;
                    const labels =
                      side === "ar"
                        ? ["实际收款", "客户退款", "净流入"]
                        : ["实际付款", "供应商退款", "净流出"];
                    return (
                      <div key={side}>
                        {card(
                          side === "ar" ? "期间收款" : "期间付款",
                          side === "ar" ? "cash_ar" : "cash_ap",
                          <>
                            <IntegrationNotice
                              status={cash.integration_status}
                              count={cash.unmapped_document_count}
                            />
                            {cash.integration_status !== "NOT_ENABLED" && (
                              <>
                                <Facts
                                  values={[
                                    [labels[0], cash.settlement_amount],
                                    [labels[1], cash.refund_amount],
                                    [labels[2], cash.net_cash_amount],
                                  ]}
                                />
                                <p className="text-sm text-muted-foreground">
                                  仅统计实际收付，期初和历史绑定不计作现金。
                                </p>
                                <details>
                                  <summary className="cursor-pointer text-sm text-primary">
                                    查看每日{side === "ar" ? "收款" : "付款"}
                                    变化
                                  </summary>
                                  <div className="mt-3">
                                    <OperationsTable
                                      headers={["日期", ...labels]}
                                      rows={cash.daily.map((day) => ({
                                        id: day.date,
                                        cells: [
                                          day.date,
                                          day.settlement_amount,
                                          day.refund_amount,
                                          day.net_cash_amount,
                                        ],
                                      }))}
                                    />
                                  </div>
                                </details>
                              </>
                            )}
                          </>,
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>
              <section aria-label="当前经营余额" className="space-y-4">
                <h2 className="text-lg font-medium">当前余额与库存</h2>
                <p className="text-sm text-muted-foreground">
                  以下是 {value.as_of} 的当前情况，不是所选日期的历史余额。
                </p>
                <div className="grid min-w-0 gap-4 xl:grid-cols-2">
                  {(["ar", "ap"] as const).map((side) => {
                    const funds =
                      side === "ar" ? value.current_ar : value.current_ap;
                    if (!(side === "ar" ? ar : ap) || !funds) return null;
                    return (
                      <div key={side}>
                        {card(
                          side === "ar" ? "当前应收" : "当前应付",
                          side === "ar" ? "current_ar" : "current_ap",
                          <>
                            <IntegrationNotice
                              status={funds.integration_status}
                              count={funds.unmapped_document_count}
                            />
                            {funds.integration_status !== "NOT_ENABLED" && (
                              <Facts
                                values={[
                                  [
                                    side === "ar" ? "尚待收款" : "尚待付款",
                                    funds.settlement_amount,
                                  ],
                                  [
                                    side === "ar"
                                      ? "尚待退给客户"
                                      : "尚待供应商退款",
                                    funds.refund_amount,
                                  ],
                                  ["往来净额", funds.balance],
                                  ["资金来源数", funds.source_count],
                                ]}
                              />
                            )}
                          </>,
                        )}
                      </div>
                    );
                  })}
                  {inventory &&
                    value.inventory &&
                    card(
                      "当前库存",
                      "inventory",
                      <>
                        <Facts
                          values={[
                            ["库存商品数", value.inventory.product_count],
                            ["低库存商品数", value.inventory.low_stock_count],
                            ...(cost
                              ? ([
                                  ["当前库存估值", value.inventory.valuation],
                                ] as [string, ReactNode][])
                              : []),
                          ]}
                        />
                        <Button
                          variant="outline"
                          onClick={() => openSources("low_stock")}
                        >
                          查看低库存商品
                        </Button>
                      </>,
                    )}
                  {replenishment &&
                    value.replenishment &&
                    card(
                      "当前补货",
                      "replenishment",
                      <>
                        <Facts
                          values={[
                            ["候选商品数", value.replenishment.candidate_count],
                            [
                              "建议补货商品数",
                              value.replenishment.suggested_count,
                            ],
                          ]}
                        />
                        <Link
                          className="text-sm text-primary underline"
                          href="/replenishment"
                        >
                          前往补货复核
                        </Link>
                      </>,
                    )}
                </div>
              </section>
              {!sale && !ar && !ap && !inventory && !replenishment && (
                <p>概览入口已开放，尚无可查看的业务指标权限。</p>
              )}
            </>
          )}
        </>
      )}
      <Dialog.Root
        open={metric !== null}
        onOpenChange={(open) => {
          if (!open) setMetric(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/25" />
          <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 max-h-[88vh] w-[min(96vw,1050px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl bg-white p-4 sm:p-6">
            <div className="flex flex-wrap justify-between gap-2">
              <Dialog.Title className="text-lg font-medium">
                {metric ? metricNames[metric] : "指标来源"}
              </Dialog.Title>
              <Button variant="outline" onClick={() => setMetric(null)}>
                关闭来源
              </Button>
            </div>
            <Dialog.Description className="mt-2 text-sm text-muted-foreground">
              按与概览相同的条件读取来源；仅展示当前权限允许的数据。
            </Dialog.Description>
            {metric && !canMetric(metric) ? (
              <p role="alert" className="mt-4">
                当前已无权查看该指标的来源。
              </p>
            ) : (
              <div className="mt-4 min-w-0 space-y-4">
                {sources.isPending && <p role="status">正在读取指标来源…</p>}
                {sources.error && <p role="alert">{sources.error.message}</p>}
                {detail && !sources.error && (
                  <>
                    <p className="text-sm">
                      {metric?.startsWith("current_") ||
                      ["inventory", "low_stock", "replenishment"].includes(
                        metric ?? "",
                      )
                        ? `当前数据时间 ${detail.as_of}`
                        : `期间 ${detail.date_from} 至 ${detail.date_to}`}{" "}
                      · {detail.business_timezone}
                    </p>
                    <IntegrationNotice
                      status={detail.integration_status}
                      count={detail.unmapped_document_count}
                    />
                    <OperationsTable
                      headers={["单号 / 商品", "往来对象", "数据", "追溯"]}
                      rows={detail.items.map((row) => ({
                        id: row.id,
                        cells: [
                          <div key="label" className="min-w-32 break-words">
                            <p>{row.number}</p>
                            <p className="text-muted-foreground">{row.label}</p>
                          </div>,
                          row.party_name ?? "—",
                          <div key="facts" className="min-w-64">
                            <Facts values={sourceValues(row)} />
                          </div>,
                          sourceLink(row),
                        ],
                      }))}
                    />
                    <div className="flex flex-wrap items-center gap-3">
                      <Button
                        variant="outline"
                        disabled={page <= 1}
                        onClick={() => setPage((p) => p - 1)}
                      >
                        上一页
                      </Button>
                      <span className="text-sm">
                        第 {page} 页 · 共 {detail.total} 条
                      </span>
                      <Button
                        variant="outline"
                        disabled={page * 25 >= detail.total}
                        onClick={() => setPage((p) => p + 1)}
                      >
                        下一页
                      </Button>
                    </div>
                  </>
                )}
              </div>
            )}
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}
