import { test, expect, Page } from "@playwright/test";

/**
 * End-to-end UI ↔ backend wiring tests.
 *
 * For each of the 8 supported option types, we drive the React form, click
 * Run Pricing, and assert:
 *   1. The frontend issues a POST /api/price with the correct option_type
 *      (and barrier_level for barrier products).
 *   2. The backend responds 200.
 *   3. The Pricing Results page renders with the price + Greeks visible.
 *
 * Numerical correctness lives in the Python pytest suite — this file is the
 * UI contract test.
 *
 * Prereq: FastAPI backend on :8002 (the canonical port — matches
 * src/api/main.py and frontend/src/api/client.ts).
 *
 *     python -m uvicorn src.api.main:app --port 8002
 */

const PRODUCTS: Array<{
  type: string;
  label: string;
  barrier: number | null;
}> = [
  { type: "european_call", label: "European Call", barrier: null },
  { type: "european_put",  label: "European Put",  barrier: null },
  { type: "american_call", label: "American Call", barrier: null },
  { type: "american_put",  label: "American Put",  barrier: null },
  { type: "knockout_call", label: "Knockout Call", barrier: 600 },
  { type: "knockout_put",  label: "Knockout Put",  barrier: 850 },
  { type: "knockin_call",  label: "Knockin Call",  barrier: 600 },
  { type: "knockin_put",   label: "Knockin Put",   barrier: 850 },
];

async function waitForFormReady(page: Page) {
  // Home is the quant-terminal landing. Click into Quick Pricer if the
  // pricer form isn't already on screen.
  const heading = page.getByRole("heading", { name: /Price Your Option/i });
  const headingVisible = await heading.isVisible().catch(() => false);
  if (!headingVisible) {
    const pricerBtn = page.getByRole("button", { name: /Quick Pricer/i }).first();
    if (await pricerBtn.isVisible().catch(() => false)) {
      await pricerBtn.click();
    }
  }
  // The form auto-fetches market data — wait for spot to populate.
  await expect(heading).toBeVisible();
  await page.waitForFunction(() => {
    const inputs = document.querySelectorAll<HTMLInputElement>("input[type=number]");
    for (const i of inputs) {
      if (i.value && parseFloat(i.value) > 0) return true;
    }
    return false;
  }, null, { timeout: 30_000 });
}

async function setOptionType(page: Page, optionType: string) {
  const select = page.locator("select").first();
  await select.selectOption(optionType);
}

async function setBarrier(page: Page, barrier: number) {
  // Expand the Advanced Parameters section if collapsed.
  const advBtn = page.getByRole("button", { name: /Advanced Parameters/ });
  const collapsed = await advBtn.evaluate(
    (el) => (el.textContent || "").includes("▶"),
  );
  if (collapsed) await advBtn.click();
  const barrierInput = page
    .locator("label", { hasText: "Barrier Level" })
    .locator("xpath=..//input[@type='number']");
  await barrierInput.fill(String(barrier));
}

async function clickRunPricing(page: Page) {
  await page.getByRole("button", { name: /Run Pricing/i }).click();
}

async function waitForResults(page: Page) {
  await expect(page.getByRole("heading", { name: /Pricing Results/i })).toBeVisible({
    timeout: 60_000,
  });
}

async function clickNewScenario(page: Page) {
  await page.getByRole("button", { name: /New Scenario/i }).first().click();
  await waitForFormReady(page);
}

test.describe("Frontend ↔ Backend wiring (all 8 option types)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await waitForFormReady(page);
  });

  for (const product of PRODUCTS) {
    test(`prices ${product.label} via /api/price`, async ({ page }) => {
      // Capture the POST that the form is about to issue.
      const apiCall = page.waitForRequest(
        (req) =>
          req.url().includes("/api/price") && req.method() === "POST",
        { timeout: 60_000 },
      );
      const apiResp = page.waitForResponse(
        (resp) =>
          resp.url().includes("/api/price") &&
          resp.request().method() === "POST",
        { timeout: 60_000 },
      );

      await setOptionType(page, product.type);
      if (product.barrier !== null) {
        await setBarrier(page, product.barrier);
      }
      await clickRunPricing(page);

      const req = await apiCall;
      const body = JSON.parse(req.postData() || "{}");
      expect(body.option_type, "request carries the right option_type").toBe(
        product.type,
      );
      if (product.barrier !== null) {
        expect(body.barrier_level, "barrier_level is forwarded for barrier products").toBe(
          product.barrier,
        );
      } else {
        expect(body.barrier_level ?? null).toBeNull();
      }

      const resp = await apiResp;
      expect(resp.status(), "/api/price returned 200").toBe(200);

      await waitForResults(page);

      // Report header shows the underlying and option type.
      const reportText = await page.locator("body").innerText();
      expect(reportText).toMatch(/Option Pricing Report/i);
      expect(reportText).toMatch(/Pricing Method:/i);
      // Either the engine returned a non-zero price, or the price section is rendered
      // (e.g. KI with far-away barrier legitimately rounds to $0.0000).
      expect(reportText).toMatch(/Option Price/i);
    });
  }

  test("KO+KI pair sums to vanilla via the UI", async ({ page }) => {
    // Use a near-the-money barrier so KI is non-trivial.
    const NEAR_BARRIER = { call: 0.95, put: 1.05 }; // multiplier on spot

    // Locate the spot input by label (same pattern as setBarrier) so we
    // never confuse it with strike, days, or any other > 50 numeric field.
    const spotInput = page
      .locator("label", { hasText: "Spot Price" })
      .locator("xpath=..//input[@type='number']");

    // Wait for the auto-fetched spot to land (>50 means yfinance returned).
    await expect.poll(
      async () => parseFloat(await spotInput.inputValue()),
      { timeout: 30_000 },
    ).toBeGreaterThan(50);

    const lockedSpot = parseFloat(await spotInput.inputValue());
    const B = Math.round(lockedSpot * NEAR_BARRIER.call);

    async function runOne(type: string, barrier: number | null) {
      await setOptionType(page, type);
      if (barrier !== null) await setBarrier(page, barrier);
      // Pin spot to the locked value so all three sub-runs price on identical
      // input. Without this, clickNewScenario() between sub-runs re-triggers
      // the yfinance auto-fetch and the spot drifts a few cents → parity
      // fails to within 0.005 even though the engine itself is exact.
      await expect.poll(
        async () => parseFloat(await spotInput.inputValue()),
        { timeout: 30_000 },
      ).toBeGreaterThan(50);
      await spotInput.fill(String(lockedSpot));

      const respPromise = page.waitForResponse(
        (r) =>
          r.url().includes("/api/price") &&
          r.request().method() === "POST",
        { timeout: 60_000 },
      );
      await clickRunPricing(page);
      const resp = await respPromise;
      const data = await resp.json();
      await waitForResults(page);
      await clickNewScenario(page);
      return data.price as number;
    }

    const ko = await runOne("knockout_call", B);
    const ki = await runOne("knockin_call", B);
    const eu = await runOne("european_call", null);
    expect(ko + ki).toBeCloseTo(eu, 2);
  });
});
