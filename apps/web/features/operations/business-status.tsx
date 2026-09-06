"use client";

import type { ReactNode } from "react";
import { AnimatedBadge, type AnimatedBadgeStatus } from "@/components/motion/animated-badge";

const tones: Record<string, AnimatedBadgeStatus> = {
  CONFIRMED: "info", PREVIEW_READY: "info", ACTIVE: "success",
  POSTED: "success", SUCCEEDED: "success", COMPLETED: "success",
  FULFILLED: "success", RECEIVED: "success", QUEUED: "warning",
  PARTIAL: "warning", PARTIAL_FAILED: "warning", RUNNING: "loading",
  PREVIEW_INVALID: "danger", BLOCKED: "danger", FAILED: "danger", REVERSED: "danger",
};

export function BusinessStatus({ status, children }: { status: string; children: ReactNode }) {
  return <AnimatedBadge status={tones[status] ?? "neutral"} size="sm" pulse={false}>
    {children}
  </AnimatedBadge>;
}
