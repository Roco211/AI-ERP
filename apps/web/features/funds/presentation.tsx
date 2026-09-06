"use client";

import type { ReactNode } from "react";
import { BusinessTable } from "@/features/operations/business-table";

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
  return <BusinessTable headers={headers} rows={rows} empty={empty} />;
}
