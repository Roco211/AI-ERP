import {
  cleanup,
  fireEvent,
  renderHook,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { usePendingNavigationGuard } from "@/features/sales/navigation";
import { historyEntry, installHistoryTracking } from "@/lib/navigation-history";

const replaceUntracked = window.history.replaceState.bind(window.history);
const removals: (() => void)[] = [];

afterEach(() => {
  cleanup();
  for (const remove of removals.splice(0)) remove();
  window.history.replaceState({}, "", "/");
});

function observe() {
  const paths: string[] = [];
  const capture = () => paths.push(window.location.pathname);
  const router = vi.fn();
  window.addEventListener("popstate", capture, true);
  window.addEventListener("popstate", router);
  removals.push(() => {
    window.removeEventListener("popstate", capture, true);
    window.removeEventListener("popstate", router);
  });
  return { paths, router };
}

async function traverse(delta: number, path: string) {
  window.history.go(delta);
  await waitFor(() => expect(window.location.pathname).toBe(path));
}

function state(section: string) {
  return { __NA: true, __PRIVATE_NEXTJS_INTERNALS_TREE: [section, "original"] };
}

test("application push and replace preserve Next state and distinct entry identities", () => {
  installHistoryTracking();
  window.history.replaceState(state("dashboard"), "", "/dashboard");
  const initial = historyEntry(window.history.state)!;
  window.history.pushState(state("sales"), "", "/sales");
  const sales = historyEntry(window.history.state)!;
  expect(sales.scope).toBe(initial.scope);
  expect(sales.index).toBe(initial.index + 1);
  expect(sales.id).not.toBe(initial.id);
  expect(window.history.state).toMatchObject(state("sales"));
  window.history.replaceState(state("sales-detail"), "", "/sales?order=one");
  expect(historyEntry(window.history.state)).toEqual(sales);
  expect(window.history.state).toMatchObject(state("sales-detail"));
  installHistoryTracking();
  window.history.pushState(state("inventory"), "", "/inventory");
  expect(historyEntry(window.history.state)!.index).toBe(sales.index + 1);
});

test.each([-1, 1])(
  "blocked history traversal %s preserves pending state and the complete unlocked history order",
  async (direction) => {
    installHistoryTracking();
    window.history.replaceState(state("dashboard"), "", "/dashboard");
    const dashboard = window.history.state;
    window.history.pushState(state("sales"), "", "/sales?order=original");
    const sales = window.history.state;
    window.history.pushState(state("inventory"), "", "/inventory");
    const inventory = window.history.state;
    await traverse(-1, "/sales");
    const length = window.history.length;
    const { paths, router } = observe();
    const view = renderHook(({ locked }) => usePendingNavigationGuard(locked), {
      initialProps: { locked: true },
    });
    window.history.go(direction);
    await waitFor(() => {
      expect(paths).toHaveLength(2);
      expect(window.location.pathname + window.location.search).toBe(
        "/sales?order=original",
      );
    });
    expect(paths).toEqual([
      direction < 0 ? "/dashboard" : "/inventory",
      "/sales",
    ]);
    expect(window.history.state).toEqual(sales);
    expect(window.history.length).toBe(length);
    expect(router).not.toHaveBeenCalled();
    view.rerender({ locked: false });
    await traverse(-1, "/dashboard");
    expect(window.history.state).toEqual(dashboard);
    await traverse(1, "/sales");
    expect(window.history.state).toEqual(sales);
    await traverse(1, "/inventory");
    expect(window.history.state).toEqual(inventory);
    expect(router).toHaveBeenCalledTimes(3);
    expect(window.history.length).toBe(length);
  },
);

test("quick repeated Back traversals restore from the latest entry without history growth or a loop", async () => {
  installHistoryTracking();
  window.history.replaceState(state("dashboard"), "", "/dashboard");
  window.history.pushState(state("customers"), "", "/customers");
  window.history.pushState(state("sales"), "", "/sales");
  window.history.pushState(state("inventory"), "", "/inventory");
  await traverse(-1, "/sales");
  const length = window.history.length;
  const saved = window.history.state;
  const { paths, router } = observe();
  const view = renderHook(({ locked }) => usePendingNavigationGuard(locked), {
    initialProps: { locked: true },
  });
  window.history.back();
  window.history.go(-2);
  await waitFor(() => {
    expect(paths.length).toBeGreaterThanOrEqual(3);
    expect(window.location.pathname).toBe("/sales");
  });
  expect(paths).toEqual(["/customers", "/dashboard", "/sales"]);
  expect(window.history.state).toEqual(saved);
  expect(window.history.length).toBe(length);
  expect(router).not.toHaveBeenCalled();
  view.rerender({ locked: false });
  await traverse(-1, "/customers");
  await traverse(-1, "/dashboard");
  await traverse(2, "/sales");
  await traverse(1, "/inventory");
  expect(window.history.length).toBe(length);
});

test("an untracked historical entry preserves the pending view without appending history", async () => {
  installHistoryTracking();
  window.history.replaceState(state("sales"), "", "/sales?order=original");
  const { router } = observe();
  renderHook(() => usePendingNavigationGuard(true));
  const length = window.history.length;
  replaceUntracked({ legacy: true }, "", "/legacy");
  fireEvent.popState(window, { state: window.history.state });
  await waitFor(() => expect(window.location.pathname).toBe("/sales"));
  expect(window.history.state).toMatchObject(state("sales"));
  expect(historyEntry(window.history.state)).not.toBeNull();
  expect(window.history.length).toBe(length);
  expect(router).not.toHaveBeenCalled();
});

test("settling the receipt during a blocked traversal still completes its restoration", async () => {
  installHistoryTracking();
  window.history.replaceState(state("dashboard"), "", "/dashboard");
  window.history.pushState(state("sales"), "", "/sales");
  const length = window.history.length;
  const { paths, router } = observe();
  const view = renderHook(({ locked }) => usePendingNavigationGuard(locked), {
    initialProps: { locked: true },
  });
  const attempted = new Promise<void>((resolve) => {
    const done = () => resolve();
    window.addEventListener("popstate", done, { capture: true, once: true });
    removals.push(() => window.removeEventListener("popstate", done, true));
  });
  // Observe before the guard (it stops later capture handlers too).
  view.rerender({ locked: false });
  view.rerender({ locked: true });
  window.history.back();
  await attempted;
  view.rerender({ locked: false });
  await waitFor(() => {
    expect(paths).toEqual(["/dashboard", "/sales"]);
    expect(window.location.pathname).toBe("/sales");
  });
  expect(router).toHaveBeenCalledTimes(1);
  expect(window.history.length).toBe(length);
  await traverse(-1, "/dashboard");
});

test("only unresolved submissions block links and page unload, and cleanup removes the guard", () => {
  const view = renderHook(() => usePendingNavigationGuard(true));
  const link = document.createElement("a");
  link.href = "/inventory";
  document.body.append(link);
  const click = new MouseEvent("click", { bubbles: true, cancelable: true });
  link.dispatchEvent(click);
  expect(click.defaultPrevented).toBe(true);
  const unload = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(unload);
  expect(unload.defaultPrevented).toBe(true);
  view.unmount();
  const clearedUnload = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(clearedUnload);
  expect(clearedUnload.defaultPrevented).toBe(false);
  link.remove();
});
