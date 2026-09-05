"use client";

import { useFundsSubmission } from "@/features/funds/use-submission";

/** Share the verified command receipt and unknown-result state machine. */
export function useOperationSubmission() {
  return useFundsSubmission({
    success: "操作已完成，记录已更新。",
    committedRefreshFailure:
      "操作已提交，详情暂时无法刷新。请重新读取已保存的记录，勿重复创建。",
  });
}
