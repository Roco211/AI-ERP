"use client";

import type { ReactNode } from "react";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";

// Display the exact server amount. Never round, total, or allocate funds here.
export const amountText = (value: string | null | undefined) => value ?? "—";
export const positiveAmount = (value: string) =>
  /^\d+(?:\.\d{1,4})?$/.test(value) && !/^0+(?:\.0*)?$/.test(value);

type Row = { id: string; cells: ReactNode[] };
export function FundsTable({
  headers,
  rows,
  empty = "暂无资金记录。",
}: {
  headers: string[];
  rows: Row[];
  empty?: string;
}) {
  const columns: ColumnDef<Row>[] = headers.map((header, index) => ({
    id: String(index),
    header,
    cell: ({ row }) => row.original.cells[index],
  }));
  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.id,
    manualPagination: true,
  });
  return (
    <div className="min-w-0 max-w-full overflow-x-auto rounded-xl border border-border bg-white">
      <table className="w-full text-left text-sm">
        <thead className="bg-[#f4f6f2] text-xs text-muted-foreground">
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => (
                <th
                  className="whitespace-nowrap px-4 py-3 font-normal"
                  key={header.id}
                >
                  {flexRender(
                    header.column.columnDef.header,
                    header.getContext(),
                  )}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr className="border-t border-border" key={row.id}>
              {row.getVisibleCells().map((cell) => (
                <td className="px-4 py-3 tabular-nums" key={cell.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && (
        <p className="p-8 text-center text-sm text-muted-foreground">{empty}</p>
      )}
    </div>
  );
}
