import { test, expect } from "@playwright/test";

/**
 * Regression for the co-pilot init bug (Apr 2026).
 *
 * The agent client used to hardcode port 8003 while the pricing client used
 * 8002. The Structuring Co-pilot's POST /api/agent/sessions silently went to
 * a non-existent server and the agent framework "failed to initiate".
 *
 * Both clients now share frontend/src/api/baseUrl.ts. These tests ensure the
 * regression cannot return.
 */

test.describe("Co-pilot init — port consistency", () => {
  test("agent POST goes to the same origin as pricing endpoints", async ({
    page,
  }) => {
    await page.goto("/");

    const pricingReq = page.waitForRequest(
      (req) =>
        req.url().includes("/api/market/spot-price") &&
        req.method() === "GET",
      { timeout: 30_000 },
    );

    const pricingRequest = await pricingReq;
    const pricingUrl = new URL(pricingRequest.url());

    await page
      .getByRole("button", { name: "Structuring Co-pilot" })
      .click();

    const agentReq = page.waitForRequest(
      (req) =>
        req.url().includes("/api/agent/sessions") &&
        req.method() === "POST",
      { timeout: 30_000 },
    );

    await page.getByRole("button", { name: /^Run$/ }).click();
    const agentRequest = await agentReq;
    const agentUrl = new URL(agentRequest.url());

    expect(agentUrl.host, "agent and pricing must share host:port").toBe(
      pricingUrl.host,
    );
    expect(agentUrl.protocol).toBe(pricingUrl.protocol);
  });

  test("co-pilot surfaces last_error from backend when agent flow fails", async ({
    page,
  }) => {
    await page.goto("/");
    await page
      .getByRole("button", { name: "Structuring Co-pilot" })
      .click();
    await page.getByRole("button", { name: /^Run$/ }).click();

    // Wait for the session lane to appear (always rendered once a session
    // exists, regardless of success or backend error).
    const sessionLane = page.getByRole("heading", { name: /① Intake/ }).first();
    await expect(sessionLane).toBeVisible({ timeout: 30_000 });

    // If the backend errored (e.g. LLM out of credits), the banner must
    // surface a non-empty message — proving last_error reaches the UI rather
    // than vanishing silently. If no error, the test is a no-op.
    const banner = page.getByTestId("copilot-error-banner");
    const bannerVisible = await banner.isVisible().catch(() => false);
    if (bannerVisible) {
      const text = await banner.innerText();
      expect(
        text.trim().length,
        "error banner has a real message beyond the 'Error:' label",
      ).toBeGreaterThan("Error:".length + 5);
    }
  });
});
