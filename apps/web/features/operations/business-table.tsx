"use client";

import { useMemo, type ReactNode } from "react";
import { Table, type TableColumn } from "@/components/motion/table";

export type BusinessRow = { id: string; cells: ReactNode[] };
export type BusinessTableProps = {
  headers: ReactNode[];
  rows: BusinessRow[];
  empty?: ReactNode;
  className?: string;
};

function columnWidth(header: ReactNode): string {
  if (typeof header !== "string") return "150px";
  if (/状态/.test(header)) return "104px";
  if (/单位/.test(header)) return "120px";
  if (/分类/.test(header)) return "140px";
  if (/操作/.test(header)) return "200px";
  if (/规格/.test(header)) return "130px";
  if (/编码|编号/.test(header)) return "190px";
  if (/商品|名称|来源|说明/.test(header)) return "220px";
  return "150px";
}

/** The API owns paging and ordering; this adapter only presents its current page. */
export function BusinessTable({ headers, rows, empty = "暂无记录。", className }: BusinessTableProps) {
  const columns = useMemo<TableColumn<BusinessRow>[]>(() => headers.map((header, index) => ({
    key: String(index),
    header,
    width: columnWidth(header),
    cell: (row) => <div className={`min-w-0 whitespace-normal break-words leading-6 tabular-nums ${typeof header === "string" && /操作/.test(header) ? "[&>div]:flex-wrap" : ""}`}>{row.cells[index]}</div>,
  })), [headers]);

  return <div className="min-w-0 max-w-full" data-business-table>
    <Table
      data={rows}
      columns={columns}
      getRowId={(row) => row.id}
      virtualized={false}
      selectable={false}
      resizable={false}
      reorderable={false}
      emptyState={empty}
      className={`rounded-2xl [&_th]:font-medium ${className ?? ""}`}
    />
  </div>;
}
