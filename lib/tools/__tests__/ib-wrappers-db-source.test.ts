import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { ibOrders } from "../wrappers/ib-orders";
import { ibSync } from "../wrappers/ib-sync";

const portfolioPayload = {
  bankroll: 100000,
  peak_value: 105000,
  last_sync: "2026-06-23T12:00:00Z",
  positions: [],
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
};

const ordersPayload = {
  last_sync: "2026-06-23T12:00:00Z",
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

function stubJsonResponse(body: unknown, status = 200) {
  const fetchMock = vi.fn(async () => (
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    })
  ));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("IB wrappers use Turso-backed FastAPI endpoints", () => {
  it("does not reference legacy flat source files", () => {
    const files = [
      "lib/tools/wrappers/ib-sync.ts",
      "lib/tools/wrappers/ib-orders.ts",
      "lib/tools/schemas/ib-sync.ts",
      "lib/tools/schemas/ib-orders.ts",
    ];
    for (const file of files) {
      const text = readFileSync(resolve(process.cwd(), file), "utf8");
      expect(text).not.toMatch(/data\/(?:portfolio|orders|trade_log|blotter|watchlist)\.json/);
      expect(text).not.toMatch(/\b(?:portfolio|orders|trade_log|blotter|watchlist)\.json\b/);
    }
  });

  it("ibSync returns the portfolio sync endpoint payload", async () => {
    const fetchMock = stubJsonResponse(portfolioPayload);

    const result = await ibSync();

    expect(result.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(String(fetchMock.mock.calls[0][0])).toContain("/portfolio/sync");
    if (result.ok) {
      expect(result.data).toEqual(portfolioPayload);
    }
  });

  it("ibOrders returns the orders refresh endpoint payload", async () => {
    const fetchMock = stubJsonResponse(ordersPayload);

    const result = await ibOrders();

    expect(result.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(String(fetchMock.mock.calls[0][0])).toContain("/orders/refresh");
    if (result.ok) {
      expect(result.data).toEqual(ordersPayload);
    }
  });

  it("ibSync reports FastAPI errors as wrapper failures", async () => {
    stubJsonResponse({ detail: "Gateway down" }, 502);

    const result = await ibSync();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.stderr).toContain("Gateway down");
    }
  });

  it("ibOrders rejects malformed endpoint payloads", async () => {
    stubJsonResponse({ status: "ok" });

    const result = await ibOrders();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.stderr).toContain("Schema validation failed");
    }
  });
});
