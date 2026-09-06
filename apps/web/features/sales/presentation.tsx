"use client";
import type { ReactNode } from "react";
import { BusinessTable } from "@/features/operations/business-table";
import Link from "next/link";
import type { PriceSource } from "./client";

export const number = (value: string | null | undefined) =>
  value == null
    ? "—"
    : value.includes(".")
      ? value.replace(/0+$/, "").replace(/\.$/, "")
      : value;
export const positive = (value: string | null | undefined) =>
  !!value && /^\d+(?:\.\d+)?$/.test(value) && !/^0+(?:\.0*)?$/.test(value);
export const names = {
  DRAFT: "草稿",
  CONFIRMED: "已确认",
  CLOSED: "已关闭",
  CANCELLED: "已取消",
  POSTED: "已过账",
  REVERSED: "已冲销",
  UNFULFILLED: "未出库",
  PARTIAL: "部分出库",
  FULFILLED: "已发齐",
};
const sourceNames: Record<PriceSource["source"], string> = {
  customer: "客户专属价",
  history: "最近有效成交价",
  retail: "客户零售价",
  wholesale: "客户批发价",
  standard: "标准价",
  manual: "手动单价",
  unset: "尚无建议价",
};
export function PriceProvenance({
  source,
  customer,
  product,
  locked,
}: {
  source?: PriceSource | null;
  customer: string;
  product: string;
  locked: boolean;
}) {
  if (!source)
    return <span className="text-xs text-muted-foreground">价格待复核</span>;
  return (
    <details className="min-w-0 text-xs text-muted-foreground">
      <summary className="cursor-pointer">
        价格来源：{sourceNames[source.source]}
      </summary>
      <div className="mt-2 space-y-1 break-all">
        {source.original_price != null && (
          <p>
            原单位价 {number(source.original_price)}；原单位换算率{" "}
            {number(source.original_factor)}
          </p>
        )}
        <p>
          所选单位换算率 {number(source.target_factor)}
          ；最终单价四舍五入保留六位小数。
        </p>
        {source.source_id && <p>来源记录：{source.source_id}</p>}
        {source.source === "history" && (
          <Link
            aria-disabled={locked}
            tabIndex={locked ? -1 : undefined}
            onClick={(event) => {
              if (locked) event.preventDefault();
            }}
            className="inline-block text-primary underline"
            href={
              source.source_document_id
                ? `/sales?document=${encodeURIComponent(source.source_document_id)}`
                : `/sales?tab=prices&customer=${encodeURIComponent(customer)}&product=${encodeURIComponent(product)}&source=${encodeURIComponent(source.source_id ?? "")}`
            }
            target="_blank"
            rel="noopener noreferrer"
          >
            查看来源成交记录
          </Link>
        )}
        {!["history", "manual", "unset"].includes(source.source) && (
          <p>以上是本次报价保留的目录价格来源快照。</p>
        )}
      </div>
    </details>
  );
}
export function Grid({
  headers,
  rows,
  empty = "暂无销售记录。",
}: {
  headers: string[];
  rows: { id: string; cells: ReactNode[] }[];
  empty?: string;
}) {
  return <BusinessTable headers={headers} rows={rows} empty={empty} />;
}
