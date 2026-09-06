"use client";

import { useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { usePendingNavigationGuard } from "@/features/sales/navigation";

type Pending = {
  key: string;
  uncertain: boolean;
  send: (key: string) => Promise<() => Promise<void>>;
};

/** Retain the original funds request until a successful receipt resolves it. */
export function useFundsSubmission(messages?: {
  success: string;
  committedRefreshFailure: string;
}) {
  const pending = useRef<Pending | null>(null);
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const locked = busy || uncertain;
  usePendingNavigationGuard(locked);

  async function execute() {
    if (inFlight.current || !pending.current) return;
    const request = pending.current;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    setConflict(false);
    let committed = false;
    try {
      const afterReceipt = await request.send(request.key);
      committed = true;
      pending.current = null;
      setUncertain(false);
      await afterReceipt();
      setNotice(messages?.success ?? "操作已完成，资金记录已更新。");
    } catch (error) {
      if (committed) {
        setNotice(
          messages?.committedRefreshFailure ??
            "操作已提交，详情暂时无法刷新。请重新加载后核对资金记录。",
        );
      } else {
        const rejected =
          error instanceof ApiError &&
          error.status >= 400 &&
          error.status < 500 &&
          !("submissionUncertain" in error && error.submissionUncertain === true);
        // A later rejection cannot establish whether the first request committed.
        if (!rejected) request.uncertain = true;
        if (rejected && !request.uncertain) {
          pending.current = null;
          setConflict(error.status === 409);
        }
        setUncertain(pending.current !== null);
        setError(
          (error instanceof Error ? error.message : "暂时无法完成操作。") +
            (pending.current ? " 提交结果待确认，请重试原提交。" : ""),
        );
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  async function submit<Result>(
    operation: (key: string) => Promise<Result>,
    onReceipt: (result: Result) => void | Promise<void>,
  ) {
    if (pending.current || inFlight.current) return;
    pending.current = {
      key: crypto.randomUUID(),
      uncertain: false,
      send: async (key) => {
        const result = await operation(key);
        return async () => {
          await onReceipt(result);
        };
      },
    };
    await execute();
  }

  return {
    busy,
    locked,
    uncertain,
    error,
    notice,
    conflict,
    submit,
    retry: execute,
    setError,
    setNotice,
    clearConflict: () => setConflict(false),
    isLocked: () => inFlight.current || pending.current !== null,
  };
}
