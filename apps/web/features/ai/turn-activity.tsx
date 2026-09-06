"use client";

import { Check, CircleAlert, CircleDashed } from "lucide-react";
import { AgentActivity, type AgentActivityItem } from "@/components/agents/agent-activity";
import { ThinkingShimmer } from "@/components/agents/loading-states/thinking-shimmer";
import type { Turn } from "./assistant-client";

/** Only server-recorded public operations belong in this disclosure. */
export function TurnActivity({ turn, interrupted = false }: { turn: Turn; interrupted?: boolean }) {
  const activity = turn.activity ?? [];
  const working = turn.state === "RUNNING" && !interrupted;
  const current = activity.findLast((item) => item.state === "running");
  if (!activity.length) return working ? <p role="status" className="text-xs text-muted-foreground">
    <ThinkingShimmer>正在等待助手响应…</ThinkingShimmer>
  </p> : null;
  const failed = activity.some((item) => item.state === "failed");
  const items: AgentActivityItem[] = activity.map((item) => ({
    id: item.id, type: "trace", kind: item.kind, label: item.title,
    icon: item.state === "complete" ? <Check className="size-3.5 text-emerald-600 dark:text-emerald-400" />
      : item.state === "failed" ? <CircleAlert className="size-3.5 text-amber-600 dark:text-amber-400" />
        : <CircleDashed className="size-3.5" />,
    detail: <span className="text-[11px] text-muted-foreground">
      {item.state === "complete" ? "已完成" : item.state === "failed" ? "未完成" : interrupted ? "待确认" : "处理中"}
    </span>,
  }));
  return <div aria-label="处理过程" className="w-full min-w-0">
    <AgentActivity items={items} status={working ? "working" : "complete"}
      collapseOnComplete={!failed && !interrupted} defaultOpen={failed || interrupted}
      activeLabel={current?.title ?? "正在处理请求…"}
      renderWorkingStatus={({ label }) => <span role="status" className="text-xs"><ThinkingShimmer>{label}</ThinkingShimmer></span>}
      summary={interrupted ? "连接已中断 · 处理结果待确认" : failed ? "处理未完成 · 查看记录" : `已完成 ${activity.length} 项处理`}
      className="text-xs" maxHeight={240} />
  </div>;
}
