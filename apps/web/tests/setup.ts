import "@testing-library/jest-dom/vitest";

// jsdom has no media-query engine. Real light/dark, touch and reduced-motion
// behavior is exercised in Playwright; unit tests use a stable desktop viewport.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string): MediaQueryList => ({
    matches: query.includes("hover: hover") || query.includes("pointer: fine"),
    media: query,
    onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {},
    dispatchEvent: () => true,
  }),
});
