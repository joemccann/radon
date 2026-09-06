import { expect, test, type Page } from "@playwright/test";

test.describe("demo workstation transport", () => {
  test.skip(process.env.NEXT_PUBLIC_RADON_DEMO !== "1", "demo-only browser contract");

  async function stubShellApis(page: Page): Promise<string[]> {
    const apiRequests: string[] = [];
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      apiRequests.push(`${request.method()} ${url.pathname}`);
      const now = new Date().toISOString();
      const body = url.pathname === "/api/portfolio"
        ? {
            bankroll: 1_000_000,
            peak_value: 1_000_000,
            last_sync: now,
            total_deployed_pct: 0,
            total_deployed_dollars: 0,
            remaining_capacity_pct: 100,
            position_count: 0,
            defined_risk_count: 0,
            undefined_risk_count: 0,
            avg_kelly_optimal: null,
            exposure: {},
            violations: [],
            positions: [],
          }
        : url.pathname === "/api/orders"
          ? { last_sync: now, open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 }
          : url.pathname === "/api/blotter"
            ? { as_of: now, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }
            : url.pathname === "/api/profile"
              ? { username: "Demo Operator" }
              : url.pathname === "/api/watchlist"
                ? { symbols: [] }
                : url.pathname === "/api/service-health"
                  ? { services: [] }
                  : url.pathname === "/api/flex-token"
                    ? { ok: true, days_until_expiry: 14 }
                    : {};
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
        body: JSON.stringify(body),
      });
    });
    return apiRequests;
  }

  test("supplies order-book depth, tape, and local ticker search without broker transport", async ({ page }, testInfo) => {
    const websocketUrls: string[] = [];
    page.on("websocket", (socket) => websocketUrls.push(socket.url()));
    const apiRequests = await stubShellApis(page);

    await page.goto("/NEM?tab=book");

    await expect(page.locator(".book-feed-pill")).toHaveText("SAMPLE SMART DEPTH");
    await expect(page.locator(".book-montage .book-row")).toHaveCount(10);
    await expect(page.locator(".book-tape-cell .book-trow")).toHaveCount(8);
    await expect(page.getByText("No real-time data", { exact: true })).toHaveCount(0);

    const search = page.getByRole("combobox", { name: "Search ticker" });
    await search.focus();
    await search.fill("SNDK");
    await expect(page.getByRole("option", { name: /SNDK/ }).first()).toBeVisible();

    await expect(page.getByRole("button", { name: "Sync now" })).toHaveCount(0);
    await expect(page.locator('[data-integrity="demo"]')).toHaveText("Sample data");
    await page.screenshot({ path: testInfo.outputPath("demo-order-book.png"), fullPage: true });

    expect(apiRequests.filter((request) => request.endsWith(" /api/ib/ws-ticket"))).toEqual([]);
    expect(websocketUrls.filter((url) => url.includes(":8765") || /\/ws(?:\?|$)/.test(url))).toEqual([]);
  });
});
