/** Application history entries retain their identity across Next's replaceState calls. */
const key = "__forge_history_v1";
type Entry = { scope: string; index: number; id: string };
let installed = false;
let traversalGuard: ((event: PopStateEvent) => void) | undefined;

export function historyEntry(state: unknown): Entry | null {
  if (!state || typeof state !== "object") return null;
  const entry = (state as Record<string, unknown>)[key];
  if (!entry || typeof entry !== "object") return null;
  const { scope, index, id } = entry as Partial<Entry>;
  return typeof scope === "string" &&
    typeof id === "string" &&
    typeof index === "number" &&
    Number.isSafeInteger(index) &&
    index >= 0
    ? { scope, index, id }
    : null;
}

function initialEntry(): Entry {
  return { scope: crypto.randomUUID(), index: 0, id: crypto.randomUUID() };
}

function withEntry(state: unknown, entry: Entry) {
  return {
    ...(state && typeof state === "object" ? state : {}),
    [key]: entry,
  };
}

export function installHistoryTracking() {
  if (installed) return;
  installed = true;
  // Native Window popstate dispatch can reach an earlier router listener before
  // a capture listener installed later. Reserve this slot at application mount,
  // before Next's passive effect; guards only change its active callback.
  window.addEventListener("popstate", (event) => traversalGuard?.(event), true);
  const push = window.history.pushState.bind(window.history);
  const replace = window.history.replaceState.bind(window.history);
  replace(
    withEntry(
      window.history.state,
      historyEntry(window.history.state) ?? initialEntry(),
    ),
    "",
  );
  // These wrappers live for this document, like the application router. Installing
  // once also prevents Strict Mode remounts from stacking wrappers or removing
  // Next's own wrappers, whichever effect was installed first.
  window.history.pushState = (state: unknown, unused, url) => {
    const previous = historyEntry(window.history.state) ?? initialEntry();
    const entry = {
      ...previous,
      index: previous.index + 1,
      id: crypto.randomUUID(),
    };
    push(withEntry(state, entry), unused, url);
  };
  window.history.replaceState = (state: unknown, unused, url) => {
    const entry = historyEntry(window.history.state) ?? initialEntry();
    replace(withEntry(state, entry), unused, url);
  };
}

export function guardHistoryTraversals(guard: (event: PopStateEvent) => void) {
  installHistoryTracking();
  traversalGuard = guard;
  return () => {
    if (traversalGuard === guard) traversalGuard = undefined;
  };
}
