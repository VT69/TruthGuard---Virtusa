import { defineConfig, devices } from "@playwright/test";

const API_PORT = Number(process.env.E2E_API_PORT ?? 8000);
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3000);

/** The e2e suite walks the 5-minute demo script against a freshly seeded API (DEMO data, offline LLM). */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } } }],
  webServer: [
    {
      command:
        `cd ../backend && rm -f .e2e.db && DATABASE_URL=sqlite+aiosqlite:///./.e2e.db TODAY_OVERRIDE=2026-10-23 ` +
        `LLM_MODE=offline LLM_CACHE_DIR=./.cache/e2e CORS_ORIGINS='["http://localhost:${WEB_PORT}"]' ` +
        `.venv/bin/uvicorn truthguard.api.app:app_factory --factory --port ${API_PORT}`,
      url: `http://localhost:${API_PORT}/readyz`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: `NEXT_PUBLIC_API_URL=http://localhost:${API_PORT} npx next start -p ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
