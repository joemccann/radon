import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = resolve(fileURLToPath(import.meta.url), "..");
const webDir = resolve(__dirname, "..");
const projectRoot = resolve(webDir, "..");

// --- Journal API Route ---

describe("GET /api/journal", () => {
  it("returns a response with trades array", async () => {
    const { GET } = await import("../app/api/journal/route");
    const response = await GET();
    const body = await response.json();

    // Route returns 200 with trades if file exists, or 500 with empty trades if not
    expect(Array.isArray(body.trades)).toBeTruthy();
    if (response.status === 200 && body.trades.length > 0) {
      const trade = body.trades[0];
      expect(typeof trade.id === "number").toBeTruthy();
      expect(typeof trade.ticker === "string").toBeTruthy();
      expect(typeof trade.structure === "string").toBeTruthy();
    }
  });
});

// --- Discover API Route ---

describe("GET /api/discover", () => {
  it("returns cached discover data or empty structure", async () => {
    const { GET } = await import("../app/api/discover/route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect("candidates" in body).toBeTruthy();
    expect(Array.isArray(body.candidates)).toBeTruthy();
    expect("candidates_found" in body).toBeTruthy();
  });
});

// --- Structural: normalizeNumber rejects negatives ---

describe("ib_realtime_server.js requests realtime market data", () => {
  it("requests realtime (type 1), not delayed-frozen, on connect", () => {
    // Flipped from delayed-frozen (type 4) to realtime (type 1) on 2026-06-14 —
    // the delayed feed made the header futures strip + every watchlist quote lag
    // the market by ~15 min. Off-hours, IB holds the last live values.
    const content = readFileSync(resolve(projectRoot, "scripts", "ib_realtime_server.js"), "utf8");
    expect(content.includes("reqMarketDataType(1)")).toBeTruthy();
    expect(content.includes("reqMarketDataType(4)")).toBeFalsy();
  });
});

// --- Structural: ib_sync.py requests frozen data ---

describe("ib_sync.py frozen market data", () => {
  it("calls set_market_data_type(4) before fetching prices", () => {
    const content = readFileSync(resolve(projectRoot, "scripts", "ib_sync.py"), "utf8");
    expect(content.includes("set_market_data_type(4)")).toBeTruthy();
  });
});

// --- Structural: cancel_order has clientId reconnect ---

describe("ib_order_manage.py cancel clientId fix", () => {
  it("cancel_order reconnects as original clientId", () => {
    const content = readFileSync(resolve(projectRoot, "scripts", "ib_order_manage.py"), "utf8");
    // Verify the clientId reconnect pattern exists in cancel_order
    expect(
      content.includes("trade.order.clientId") && content.includes("client.disconnect()"),
    ).toBeTruthy();
  });

  it("cancel_order captures IB error events", () => {
    const content = readFileSync(resolve(projectRoot, "scripts", "ib_order_manage.py"), "utf8");
    expect(content.includes("errorEvent += on_error")).toBeTruthy();
    // Verify it checks for error 10147
    expect(content.includes("10147")).toBeTruthy();
  });
});

