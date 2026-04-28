import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the derivatives pricer end-to-end tests.
 *
 * Assumes the FastAPI backend is already running on :8003 (see
 * `python -m uvicorn src.api.main:app --port 8003`). Vite dev server is
 * started by Playwright via `webServer` below.
 *
 * Run:    npx playwright test
 * Headed: npx playwright test --headed
 */
export default defineConfig({
  testDir: "./tests",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false, // pricing requests are not free; serialise to keep backend logs sane
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    timeout: 60_000,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
