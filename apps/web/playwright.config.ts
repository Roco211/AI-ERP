import { defineConfig, devices } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// Only pass local seed credentials to the test process; never write them to a report.
for (const line of readFileSync(resolve(__dirname, "../../.env"), "utf8").split("\n")) {
  const split = line.indexOf("=");
  if (split > 0) { const key = line.slice(0, split); process.env[key] ??= line.slice(split + 1); }
}
export default defineConfig({
  testDir: "./e2e", fullyParallel: false, workers: 1, retries: 0,
  use: { baseURL: "http://localhost:3100", trace: "off", screenshot: "only-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    { command: "uv run --project ../api uvicorn forge_erp.main:app --host 127.0.0.1 --port 8100 --no-access-log", url: "http://127.0.0.1:8100/healthz", reuseExistingServer: !process.env.CI, timeout: 120000 },
    { command: "pnpm start", url: "http://localhost:3100/login", reuseExistingServer: !process.env.CI, timeout: 120000 }
  ]
});
