/**
 * E2E: iPhone PWA resume race. The first Transmit after resume reaches the
 * middleware with an expired Clerk session cookie and is rejected 401
 * (x-clerk-auth-status: signed-out) before the route handler runs. The ticket
 * must refresh the session, resend the identical order once, show success,
 * and never render "Your session has expired".
 *
 * Clerk-js cannot load in the hermetic e2e run (stub publishable key), so a
 * minimal signed-in window.Clerk is installed before the app boots; the
 * session's getToken is the refresh the interceptor calls.
 */

import { test, expect, type Page } from "@playwright/test";

async function installSignedInClerk(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as Record<string, unknown>;
    let tokenCalls = 0;
    const listeners = new Set<(e: unknown) => void>();
    const session = {
      id: "sess_e2e",
      status: "active",
      getToken: async () => { tokenCalls += 1; return `fresh-${tokenCalls}`; },
    };
    const user = { id: "user_e2e" };
    const clerk = {
      loaded: true,
      status: "ready",
      version: "e2e",
      sdkMetadata: {},
      session,
      user,
      client: { sessions: [session], signedInSessions: [session], lastActiveSessionId: "sess_e2e" },
      organization: null,
      load: async () => {},
      addListener: (fn: (e: unknown) => void) => {
        listeners.add(fn);
        fn({ client: clerk.client, session, user, organization: null });
        return () => listeners.delete(fn);
      },
      // IsomorphicClerk resolves useAuth().getToken on a "ready" status event.
      on: (event: string, handler: (status: string) => void, opts?: { notify?: boolean }) => {
        if (event === "status" && opts?.notify) handler("ready");
      },
      off: () => {},
      __internal_getOption: () => undefined,
      telemetry: undefined,
    };
    w.Clerk = clerk;
    w.__clerkTokenCalls = () => tokenCalls;
  });
}

async function stubApis(page: Page) {
  const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/portfolio", (r) => r.fulfill(json({ positions: [], last_sync: new Date().toISOString(), bankroll: 0, peak_value: 0, total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100, position_count: 0, defined_risk_count: 0, undefined_risk_count: 0, avg_kelly_optimal: null, exposure: {}, violations: [] })));
  await page.route("**/api/orders", (r) => r.fulfill(json({ open_orders: [], executed_orders: [], open_count: 0, executed_count: 0, last_sync: new Date().toISOString() })));
  await page.route("**/api/regime", (r) => r.fulfill(json({ score: 15, cri: { score: 15 } })));
  await page.route("**/api/blotter", (r) => r.fulfill(json({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] })));
  await page.route("**/api/flex-token", (r) => r.fulfill(json({ ok: true, days_until_expiry: 14 })));
  await page.route("**/api/ticker/**", (r) => r.fulfill(json({ uw_info: { name: "Apple Inc.", sector: "Tech", description: "" }, stock_state: {}, profile: {}, stats: {} })));
  await page.route("**/api/options/expirations*", (r) => r.fulfill(json({ symbol: "AAPL", expirations: ["20260320", "20260417"] })));
  await page.route("**/api/options/chain*", (r) => r.fulfill(json({ symbol: "AAPL", expiry: "20260320", exchange: "SMART", strikes: [195, 200, 205, 210, 215], multiplier: "100" })));
  await page.route("**/api/prices**", (r) => r.abort());
}

test("transmit after an expired session token refreshes and places the order once", async ({ page }, testInfo) => {
  await installSignedInClerk(page);
  await stubApis(page);

  const placeRequests: Array<{ url: string; method: string; body: unknown }> = [];
  await page.route("**/api/orders/place", async (route, request) => {
    placeRequests.push({ url: new URL(request.url()).pathname, method: request.method(), body: request.postDataJSON() });
    if (placeRequests.length === 1) {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        headers: {
          "x-clerk-auth-status": "signed-out",
          "x-clerk-auth-reason": "session-token-expired-refresh-non-eligible-non-get",
        },
        body: JSON.stringify({ error: "Unauthorized", code: "UNAUTHORIZED" }),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok", orderId: 9999, permId: 1234, initialStatus: "Submitted" }) });
  });

  await page.goto("/AAPL?tab=chain");
  await page.getByTestId("mobile-chain-call-200").click();
  await page.getByTestId("mobile-chain-detail-buy").click();
  await page.getByTestId("mobile-chain-pending-strip").click();
  await page.getByTestId("mobile-order-ticket-price-input").fill("3.45");
  await page.getByTestId("mobile-order-ticket-review").click();
  expect(placeRequests).toHaveLength(0);
  await page.getByTestId("mobile-order-ticket-submit").click();

  await expect(page.getByTestId("mobile-order-ticket-success")).toBeVisible();
  await expect(page.getByText("Your session has expired")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("mobile-ticket-session-refresh.png") });

  expect(placeRequests).toHaveLength(2);
  expect(await page.evaluate(() => (window as unknown as { __clerkTokenCalls: () => number }).__clerkTokenCalls())).toBeGreaterThanOrEqual(1);
  for (const req of placeRequests) {
    expect(req.url).toBe("/api/orders/place");
    expect(req.method).toBe("POST");
    expect(req.body).toEqual(placeRequests[0].body);
  }
  expect(placeRequests[0].body).toMatchObject({ type: "option", symbol: "AAPL", action: "BUY", quantity: 1, strike: 200, right: "CALL", tif: "DAY", limitPrice: 3.45 });
});
