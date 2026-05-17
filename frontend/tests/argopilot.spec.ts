import { test, expect, Page } from "@playwright/test";

/**
 * ArgoPilot platform — UI regression suite.
 *
 * Covers the platform layer (Header, IndexTickerStrip, MoversGrid,
 * click-to-prefill, PayoffChart, GreeksBar) and asserts the existing
 * pricer + co-pilot mode-switch still work alongside it.
 *
 * Prereq: FastAPI backend running on :8002 (or wherever the frontend
 * client.ts is pointed). The webServer block in playwright.config.ts
 * boots the Vite dev server automatically.
 */

async function waitForMoversLoaded(page: Page) {
  // Wait for the movers grid to render with at least one row in any column.
  await page.waitForSelector('[data-testid="vd-movers-grid"]', { timeout: 30_000 });
  await page.waitForFunction(
    () => document.querySelectorAll(".vd-mover-row").length > 0,
    null,
    { timeout: 30_000 },
  );
}

async function waitForFormReady(page: Page) {
  // The redesigned landing lands on the home workspace selector. If the
  // pricer form isn't already on screen, click the Quick Pricer
  // workspace card (or pipeline tab) first.
  const heading = page.getByRole("heading", { name: /Price Your Option/i });
  const headingVisible = await heading.isVisible().catch(() => false);
  if (!headingVisible) {
    const pricerBtn = page.getByRole("button", { name: /Quick Pricer/i }).first();
    if (await pricerBtn.isVisible().catch(() => false)) {
      await pricerBtn.click();
    }
  }
  await expect(heading).toBeVisible();
  await page.waitForFunction(
    () => {
      const inputs = document.querySelectorAll<HTMLInputElement>("input[type=number]");
      for (const i of inputs) if (i.value && parseFloat(i.value) > 0) return true;
      return false;
    },
    null,
    { timeout: 30_000 },
  );
}

test.describe("ArgoPilot — header & status pill", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("header renders with brand mark and clock", async ({ page }) => {
    await expect(page.getByTestId("vd-header")).toBeVisible();
    await expect(page.getByText(/^ArgoPilot$/)).toBeVisible();
    // Status pill is one of two states.
    const pill = page.locator(".vd-status-pill");
    await expect(pill).toBeVisible();
    const text = (await pill.innerText()).toLowerCase();
    expect(text).toMatch(/market (open|closed)/);
    // Clock advances at least once within ~2s.
    const t0 = await page.locator(".vd-clock").innerText();
    await page.waitForTimeout(1500);
    const t1 = await page.locator(".vd-clock").innerText();
    expect(t1).not.toEqual(t0);
  });
});

test.describe("ArgoPilot — index ticker strip", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await waitForMoversLoaded(page);
  });

  test("renders index cards with sparklines", async ({ page }) => {
    const strip = page.getByTestId("vd-index-strip");
    await expect(strip).toBeVisible();
    const cards = strip.locator(".vd-index-card");
    // Responsive viewports may hide some — at minimum the first 3 are present.
    expect(await cards.count()).toBeGreaterThanOrEqual(3);

    // First card has a recharts SVG sparkline and a price.
    const first = cards.first();
    await expect(first.locator("svg")).toBeVisible();
    await expect(first.locator(".vd-index-price")).toContainText(/\$[0-9]/);
    // Change indicator is up (▲) or down (▼).
    const change = await first.locator(".vd-index-change").innerText();
    expect(change).toMatch(/^[▲▼]/);
  });
});

test.describe("ArgoPilot — movers grid", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await waitForMoversLoaded(page);
  });

  test("three columns render with rows and the grid is interactive", async ({ page }) => {
    const grid = page.getByTestId("vd-movers-grid");
    await expect(grid).toBeVisible();
    await expect(grid.locator(".vd-mover-col--gain")).toBeVisible();
    await expect(grid.locator(".vd-mover-col--lose")).toBeVisible();
    await expect(grid.locator(".vd-mover-col--vol")).toBeVisible();

    // Each visible column has at least one clickable row.
    expect(await grid.locator(".vd-mover-col--gain .vd-mover-row").count()).toBeGreaterThan(0);
    expect(await grid.locator(".vd-mover-col--lose .vd-mover-row").count()).toBeGreaterThan(0);
    expect(await grid.locator(".vd-mover-col--vol .vd-mover-row").count()).toBeGreaterThan(0);
  });

  test("clicking a gainer prefills the pricer ticker and spot", async ({ page }) => {
    // Read the ticker label of the first gainer before clicking.
    const firstGainer = page.locator(".vd-mover-col--gain .vd-mover-row").first();
    const ticker = (
      await firstGainer.locator(".vd-mover-ticker").innerText()
    ).trim();
    expect(ticker.length).toBeGreaterThan(0);

    await firstGainer.click();

    // Form should now be on the page with the underlying input set to that ticker.
    await waitForFormReady(page);
    const tickerInput = page.locator('input[type="text"]').first();
    await expect(tickerInput).toHaveValue(ticker.replace("^", ""));

    // Spot price is positive.
    const numbers = page.locator('input[type="number"]');
    const firstNumValue = await numbers.first().inputValue();
    expect(parseFloat(firstNumValue)).toBeGreaterThan(0);
  });
});

test.describe("ArgoPilot — payoff chart and Greeks bar appear after pricing", () => {
  test("submits a European Call and renders both charts", async ({ page }) => {
    await page.goto("/");
    await waitForFormReady(page);

    // Switch to European Call (cheap and fast).
    await page.locator("select").first().selectOption("european_call");

    // Run pricing.
    await page.getByRole("button", { name: /Run Pricing/i }).click();
    await expect(page.getByRole("heading", { name: /Pricing Results/i })).toBeVisible({
      timeout: 60_000,
    });

    // Both chart cards render with a recharts SVG inside.
    const payoff = page.locator(".vd-chart-card").filter({ hasText: /Payoff/i });
    const greeks = page.locator(".vd-chart-card").filter({ hasText: /Greeks/i });
    await expect(payoff).toBeVisible();
    await expect(greeks).toBeVisible();
    await expect(payoff.locator("svg").first()).toBeVisible();
    await expect(greeks.locator("svg").first()).toBeVisible();
  });
});

test.describe("ArgoPilot — regressions on existing features", () => {
  test("mode switcher Pricer ↔ Co-pilot still works", async ({ page }) => {
    await page.goto("/");

    const pricerBtn = page.getByRole("button", { name: "Quick Pricer" });
    const copilotBtn = page.getByRole("button", { name: "Structuring Co-pilot" });

    await expect(pricerBtn).toBeVisible();
    await expect(copilotBtn).toBeVisible();

    await copilotBtn.click();
    await expect(page.locator(".copilot-panel").first()).toBeVisible();

    await pricerBtn.click();
    await expect(page.getByRole("heading", { name: /Price Your Option/i })).toBeVisible();
  });

  test("Asian Call option type runs through the pipeline", async ({ page }) => {
    await page.goto("/");
    await waitForFormReady(page);

    await page.locator("select").first().selectOption("asian_call");

    const respPromise = page.waitForResponse(
      (r) => r.url().includes("/api/price") && r.request().method() === "POST",
      { timeout: 60_000 },
    );
    await page.getByRole("button", { name: /Run Pricing/i }).click();
    const resp = await respPromise;
    expect(resp.status()).toBe(200);

    const body = await resp.json();
    expect(body.price).toBeGreaterThanOrEqual(0);
    expect(typeof body.greeks).toBe("object");

    await expect(page.getByRole("heading", { name: /Pricing Results/i })).toBeVisible();
  });

  test("Lookback Call option type runs through the pipeline", async ({ page }) => {
    await page.goto("/");
    await waitForFormReady(page);

    await page.locator("select").first().selectOption("lookback_call");

    const respPromise = page.waitForResponse(
      (r) => r.url().includes("/api/price") && r.request().method() === "POST",
      { timeout: 60_000 },
    );
    await page.getByRole("button", { name: /Run Pricing/i }).click();
    const resp = await respPromise;
    expect(resp.status()).toBe(200);

    const body = await resp.json();
    expect(body.price).toBeGreaterThanOrEqual(0);
  });
});

test.describe("ArgoPilot — backend movers endpoint", () => {
  test("/api/market/movers returns the expected shape", async ({ request }) => {
    const resp = await request.get("http://localhost:8002/api/market/movers");
    expect(resp.status()).toBe(200);
    const body = await resp.json();

    expect(body).toHaveProperty("as_of");
    expect(body).toHaveProperty("indices");
    expect(body).toHaveProperty("gainers");
    expect(body).toHaveProperty("losers");
    expect(body).toHaveProperty("volatile");
    expect(Array.isArray(body.indices)).toBe(true);
    expect(Array.isArray(body.gainers)).toBe(true);

    if (body.indices.length > 0) {
      const first = body.indices[0];
      expect(first).toHaveProperty("ticker");
      expect(first).toHaveProperty("price");
      expect(first).toHaveProperty("change_pct");
      expect(first).toHaveProperty("spark");
      expect(Array.isArray(first.spark)).toBe(true);
    }
  });
});
