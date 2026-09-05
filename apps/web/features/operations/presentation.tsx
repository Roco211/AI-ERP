"use client";

import type { ComponentProps, ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { FundsTable } from "@/features/funds/presentation";
import type { useOperationSubmission } from "./use-submission";

export const selectClass =
  "h-10 min-w-0 max-w-full rounded-md border bg-white px-3 text-sm";

export function OperationsTable(props: ComponentProps<typeof FundsTable>) {
  return <FundsTable empty="暂无记录。" {...props} />;
}

export function Facts({ values }: { values: [string, ReactNode][] }) {
  return (
    <dl className="grid min-w-0 grid-cols-2 gap-3 text-sm sm:grid-cols-3">
      {values.map(([label, value]) => (
        <div key={label}>
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="break-all tabular-nums">{value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function SubmissionFeedback({
  value,
  onRefresh,
}: {
  value: ReturnType<typeof useOperationSubmission>;
  onRefresh?: () => void;
}) {
  return (
    <>
      {value.error && (
        <p
          role="alert"
          className="rounded-lg bg-red-50 p-3 text-sm text-red-800"
        >
          {value.error}
        </p>
      )}
      {value.notice && (
        <p role="status" className="text-sm text-primary">
          {value.notice}
        </p>
      )}
      {value.uncertain && (
        <div className="space-y-2 text-sm">
          <p>请保留当前页面，核对原提交结果，勿发起重复操作。</p>
          <Button disabled={value.busy} onClick={() => void value.retry()}>
            重试原提交
          </Button>
        </div>
      )}
      {value.conflict && onRefresh && (
        <Button variant="outline" disabled={value.locked} onClick={onRefresh}>
          刷新后重新复核
        </Button>
      )}
    </>
  );
}
