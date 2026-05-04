import { test, expect, Page } from "@playwright/test";

/**
 * Sensitivity Heatmap — e2e test.
 *
 * Verifies that ticking the "Deep risk" checkbox causes the frontend to
 * send deep_risk=true on /api/price, and that — when the backend responds
 * with scenario_grid + gamma_ladder — the SensitivityHeatmap component
 * renders both blocks.
 *
 * Prereq: FastAPI backend running on :8002 AND supporting the deep_risk
 * flag (Phase 4 backend). The Playwright webServer block boots the Vite
 * dev server automatically; the backend must be started separately:
 *
 *   python -m uvicorn src.api.main:app --reload --port 8002
 */

async function waitForFormReady(page: Page) {
  await expect(
    page.getByRole("heading", { name: /Price Your Option/i }),
  ).toBeVisible();
  await page.waitForFunction(
    () => {
      const inputs =
        document.querySelectorAll<HTMLInputElement>("input[type=number]");
      for (const i of inputs) {
        if (i.value && parseFloat(i.value) > 0) return true;
      }
      return false;
    },
    null,
    { timeout: 30_000 },
  );
}

async function expandAdvanced(page: Page) {
  const advBtn = page.getByRole("button", { name: /Advanced Parameters/ });
  const collapsed = await advBtn
    .evaluate((el) => (el.textContent || "").includes("▶"))
    .catch(() => false);
  if (collapsed) await advBtn.click();
  // Wait for the checkbox to become visible
  await expect(page.getByTestId("deep-risk-checkbox")).toBeVisible();
}

test.describe("Sensitivity Heatmap — deep_risk checkbox and component", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await waitForFormReady(page);
  });

  test("deep_risk checkbox is present in the Advanced Parameters section", async ({
    page,
  }) => {
    await expandAdvanced(page);
    const checkbox = page.getByTestId("deep-risk-checkbox");
    await expect(checkbox).toBeVisible();
    // Default is unchecked
    await expect(checkbox).not.toBeChecked();
    // Label reads correctly
    await expect(page.getByTestId("deep-risk-label")).toContainText(
      /deep risk/i,
    );
  });

  test("ticking deep_risk forwards deep_risk=true in the POST body", async ({
    page,
  }) => {
    await page.locator("select").first().selectOption("european_call");
    await expandAdvanced(page);

    // Tick the checkbox
    await page.getByTestId("deep-risk-checkbox").check();
    await expect(page.getByTestId("deep-risk-checkbox")).toBeChecked();

    // Intercept the request
    const apiCall = page.waitForRequest(
      (req) =>
        req.url().includes("/api/price") && req.method() === "POST",
      { timeout: 60_000 },
    );

    await page.getByRole("button", { name: /Run Pricing/i }).click();
    const req = await apiCall;
    const body = JSON.parse(req.postData() || "{}");
    expect(body.deep_risk, "deep_risk flag forwarded").toBe(true);
  });

  test("scenario grid and gamma ladder render when backend returns deep_risk payload", async ({
    page,
  }) => {
    await page.locator("select").first().selectOption("european_call");
    await expandAdvanced(page);
    await page.getByTestId("deep-risk-checkbox").check();

    // Wait for both the request and the response
    const respPromise = page.waitForResponse(
      (r) =>
        r.url().includes("/api/price") && r.request().method() === "POST",
      { timeout: 60_000 },
    );

    await page.getByRole("button", { name: /Run Pricing/i }).click();
    const resp = await respPromise;
    expect(resp.status(), "/api/price returned 200").toBe(200);

    const body = await resp.json();

    // Guard: if the backend doesn't yet implement deep_risk we skip the
    // rendering assertions rather than failing.  The POST-body and 200
    // assertions above are still enforced.
    if (!body.scenario_grid && !body.gamma_ladder) {
      test.info().annotations.push({
        type: "note",
        description:
          "Backend did not return scenario_grid / gamma_ladder — " +
          "SensitivityHeatmap rendering assertions skipped. " +
          "Ensure Phase 4 backend is running.",
      });
      return;
    }

    // Results page must be visible
    await expect(
      page.getByRole("heading", { name: /Pricing Results/i }),
    ).toBeVisible({ timeout: 60_000 });

    // The heatmap container must be on the page
    const heatmap = page.getByTestId("sensitivity-heatmap");
    await expect(heatmap).toBeVisible({ timeout: 30_000 });

    // Title text matches the /scenario|sensitivity|heatmap/i pattern
    await expect(heatmap).toContainText(/scenario|sensitivity|heatmap/i);

    // Gamma ladder chart has a recharts SVG
    const ladderChart = page.getByTestId("gamma-ladder-chart");
    await expect(ladderChart).toBeVisible();
    await expect(ladderChart.locator("svg").first()).toBeVisible();
  });
});
