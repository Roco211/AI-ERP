"use client";
import { useEffect } from "react";
import {
  guardHistoryTraversals,
  historyEntry,
  installHistoryTracking,
} from "@/lib/navigation-history";

const owners = new Set<symbol>();
let releaseProtection: (() => void) | undefined;

/** Programmatic navigation and logout must retain every unresolved submission. */
export function runGuardedNavigation(navigate: () => void): boolean {
  if (owners.size > 0) return false;
  navigate();
  return true;
}

function protectPendingNavigation() {
  // The provider normally installs tracking first. This also supports a guard
  // mounted on the first application render, before the provider's effect.
  installHistoryTracking();
  const href = window.location.href;
  const state: unknown = window.history.state;
  let origin = historyEntry(state)!;
  let restoreTimer: number | undefined;
  const navigation = (event: MouseEvent) => {
    if (event.target instanceof Element && event.target.closest("a[href]")) {
      event.preventDefault();
      event.stopPropagation();
    }
  };
  const leave = (event: BeforeUnloadEvent) => {
    event.preventDefault();
  };
  const restore = () => {
    restoreTimer = undefined;
    const current = historyEntry(window.history.state);
    if (current?.id === origin.id && current.scope === origin.scope) return;
    if (current?.scope === origin.scope && current.index !== origin.index) {
      window.history.go(origin.index - current.index);
    } else {
      // An entry created before application tracking has no trustworthy delta.
      // Keep the pending closure mounted without appending/truncating history;
      // only this unknown entry's URL/state is replaced. Its prior URL cannot
      // be restored reliably, unlike ordinary tracked application entries.
      window.history.replaceState(state, "", href);
      origin = historyEntry(window.history.state)!;
    }
  };
  const historyNavigation = (event: PopStateEvent) => {
    // Both the attempted traversal and the restoration event must precede
    // Next's router, so neither can unmount the unresolved submission.
    event.stopImmediatePropagation();
    window.clearTimeout(restoreTimer);
    const current = historyEntry(window.history.state);
    if (current?.id === origin.id && current.scope === origin.scope) {
      restoreTimer = undefined;
      return;
    }
    // Coalesce quick Back/Forward presses and calculate from the latest actual
    // entry, rather than enqueueing one stale relative traversal per event.
    restoreTimer = window.setTimeout(restore, 16);
  };
  document.addEventListener("click", navigation, true);
  window.addEventListener("beforeunload", leave);
  const removeHistoryGuard = guardHistoryTraversals(historyNavigation);
  return () => {
    if (restoreTimer !== undefined) {
      window.clearTimeout(restoreTimer);
      // A receipt may settle while a blocked traversal is being restored.
      // Finish returning to the original entry before releasing the guard.
      restore();
    }
    document.removeEventListener("click", navigation, true);
    window.removeEventListener("beforeunload", leave);
    removeHistoryGuard();
  };
}

/** Share one history origin until the last unresolved submission has settled. */
export function usePendingNavigationGuard(locked: boolean) {
  useEffect(() => {
    if (!locked) return;
    const owner = Symbol();
    owners.add(owner);
    if (owners.size === 1) releaseProtection = protectPendingNavigation();
    return () => {
      owners.delete(owner);
      if (owners.size === 0) {
        releaseProtection?.();
        releaseProtection = undefined;
      }
    };
  }, [locked]);
}
