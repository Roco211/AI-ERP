import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";
export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL(".", import.meta.url)) } },
  // Keep DOM workers bounded alongside the database and production-build checks.
  test: { maxWorkers: 2, environment: "jsdom", include: ["tests/**/*.test.tsx"], setupFiles: ["./tests/setup.ts"] }
});
