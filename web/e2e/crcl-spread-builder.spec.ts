import { expect, test } from "@playwright/test";
import { installMockWebSocket, stubApis } from "./crcl-spread-builder-fixtures";

const FIXTURE_TIME = "2026-05-04T14:00:00Z";

test.describe("CRCL chain — auto-focus existing-position expiry + positive single-leg quotes", () => {
  test.beforeEach(async ({ page }) => {
    await page.clock.setFixedTime(new Date(FIXTURE_TIME));
  });

  test("chain defaults to position expiry 20260618 (not the next ≥7-day Friday 20260612)", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await installMockWebSocket(page, FIXTURE_TIME);
    await stubApis(page, FIXTURE_TIME);

    // No positionId in URL — the fix must auto-pass focusPosition based on
    // the matching ticker, not require ?positionId=2.
    await page.goto("/CRCL?tab=chain");

    const detail = page.locator(".ticker-detail-page");
    await detail.waitFor({ timeout: 10_000 });
    await detail.locator(".chain-grid").first().waitFor();
    await expect(page.locator('select[aria-label="Options expiry"] option[value="20260618"]')).not.toContainText("(-");

    // The chain should be on 06-18 (matching position), NOT 06-12.
    // Inspect a strike row that exists in both to confirm we got 06-18 prices.
    // Since both expiries have the same strikes in this fixture, easiest tell
    // is the "expiry: 2026-06-18" indicator somewhere on the page.
    await expect(detail).toContainText(/2026-06-18|06\/18\/2026|JUN 18/i);
  });

  for (const action of ["BUY", "SELL"] as const) test(`${action} call preserves native quote sides and reviewed order payload`, async ({ page, baseURL }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await installMockWebSocket(page, FIXTURE_TIME);
    await stubApis(page, FIXTURE_TIME);

    await page.goto("/CRCL?tab=chain");

    const detail = page.locator(".ticker-detail-page");
    await detail.waitFor({ timeout: 10_000 });
    await detail.locator(".chain-grid").first().waitFor();
    await expect(page.locator('select[aria-label="Options expiry"] option[value="20260618"]')).not.toContainText("(-");

    await expect(page).toHaveURL(/expiry=2026-06-18/);
    const requests: unknown[] = [];
    await page.route("**/api/orders/place", route => {
      requests.push({ url: route.request().url(), method: route.request().method(), body: route.request().postDataJSON() });
      return route.fulfill({ json: { status: "ok", orderId: 2003, initialStatus: "Submitted" } });
    });
    const row140 = detail.getByRole("row", { name: /\$140\.00/ }).first();
    await row140.waitFor({ timeout: 5_000 });

    await row140.locator(action === "SELL" ? '.chain-bid[title="Sell call"]' : '.chain-ask[title="Buy call"]').click();

    const orderBuilder = detail.locator(".order-builder");
    await expect(orderBuilder).toBeVisible();

    await expect(orderBuilder.getByRole("button", { name: action, exact: true })).toBeVisible();
    await expect(orderBuilder.locator(".modify-price-input")).toHaveValue("8.00");
    for (const quote of ["BID 7.00", "MID 8.00", "ASK 9.00"]) {
      await expect(orderBuilder.getByRole("button", { name: quote, exact: true })).toBeEnabled();
    }
    expect(requests).toHaveLength(0);
    await orderBuilder.getByTestId("ticket-verify").click();
    expect(requests).toHaveLength(0);
    // Existing lower-strike long calls bound this one-contract short call.
    await expect(orderBuilder.getByTestId("ticket-unbounded-ack")).toHaveCount(0);
    await expect(orderBuilder.getByTestId("ticket-transmit")).toBeEnabled();
    await orderBuilder.getByTestId("ticket-transmit").click();
    await expect.poll(() => requests.length).toBe(1);
    expect(requests).toEqual([{ url: new URL("/api/orders/place", baseURL).href, method: "POST", body: { type: "option", symbol: "CRCL", action, quantity: 1, limitPrice: 8, tif: "DAY", expiry: "20260618", strike: 140, right: "CALL" } }]);
  });
});
