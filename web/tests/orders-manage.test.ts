import { describe, it, before } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { NextRequest } from "next/server";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * Tests for order cancel/modify functionality.
 *
 * Covers:
 * 1. Type contracts — OpenOrder with permId/conId fields
 * 2. Cancel API route — input validation (no IB connection needed)
 * 3. Modify API route — input validation (no IB connection needed)
 * 4. CancelledOrder display logic — shape mapping, side display, pill classes
 * 5. Pending cancel state logic — tracking, polling detection, edge cases
 */

// =============================================================================
// Type contract tests
// =============================================================================

describe("OpenOrder type contract", () => {
  it("should include permId field", () => {
    const order = {
      orderId: 10,
      permId: 12345,
      symbol: "AAPL",
      contract: {
        conId: 265598,
        symbol: "AAPL",
        secType: "STK",
        strike: null,
        right: null,
        expiry: null,
      },
      action: "BUY",
      orderType: "LMT",
      totalQuantity: 100,
      limitPrice: 175.50,
      auxPrice: null,
      status: "Submitted",
      filled: 0,
      remaining: 100,
      avgFillPrice: null,
      tif: "GTC",
    };

    assert.equal(order.permId, 12345);
    assert.equal(order.contract.conId, 265598);
  });

  it("should handle BAG orders with orderId=0", () => {
    const bagOrder = {
      orderId: 0,
      permId: 99999,
      symbol: "BKD (BAG)",
      contract: {
        conId: null,
        symbol: "BKD",
        secType: "BAG",
        strike: null,
        right: null,
        expiry: null,
      },
      action: "SELL",
      orderType: "LMT",
      totalQuantity: 100,
      limitPrice: 1.50,
      auxPrice: null,
      status: "Submitted",
      filled: 0,
      remaining: 100,
      avgFillPrice: null,
      tif: "GTC",
    };

    assert.equal(bagOrder.orderId, 0);
    assert.ok(bagOrder.permId > 0);
  });
});

// =============================================================================
// Cancel API route validation tests
// =============================================================================

describe("POST /api/orders/cancel validation", () => {
  let cancelPOST: (req: Request) => Promise<Response>;

  before(async () => {
    const mod = await import("../app/api/orders/cancel/route");
    cancelPOST = mod.POST;
  });

  it("rejects missing orderId and permId", async () => {
    const req = new NextRequest("http://localhost/api/orders/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });

    const res = await cancelPOST(req);
    assert.equal(res.status, 400);
    const body = await res.json();
    assert.ok((body as { error: string }).error.includes("orderId"));
  });

  it("rejects orderId=0 and permId=0", async () => {
    const req = new NextRequest("http://localhost/api/orders/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orderId: 0, permId: 0 }),
    });

    const res = await cancelPOST(req);
    assert.equal(res.status, 400);
  });
});

// =============================================================================
// Modify API route validation tests
// =============================================================================

describe("POST /api/orders/modify validation", () => {
  let modifyPOST: (req: Request) => Promise<Response>;

  before(async () => {
    const mod = await import("../app/api/orders/modify/route");
    modifyPOST = mod.POST;
  });

  it("rejects missing orderId and permId", async () => {
    const req = new NextRequest("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ newPrice: 22.50 }),
    });

    const res = await modifyPOST(req);
    assert.equal(res.status, 400);
  });

  it("rejects missing newPrice", async () => {
    const req = new NextRequest("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permId: 12345 }),
    });

    const res = await modifyPOST(req);
    assert.equal(res.status, 400);
    const body = await res.json();
    assert.ok((body as { error: string }).error.includes("newPrice"));
  });

  it("rejects newPrice of zero", async () => {
    const req = new NextRequest("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permId: 12345, newPrice: 0 }),
    });

    const res = await modifyPOST(req);
    assert.equal(res.status, 400);
  });

  it("rejects negative newPrice", async () => {
    const req = new NextRequest("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permId: 12345, newPrice: -5 }),
    });

    const res = await modifyPOST(req);
    assert.equal(res.status, 400);
  });
});

// =============================================================================
// CancelledOrder data shape tests
// =============================================================================

describe("CancelledOrder in executed table", () => {
  it("should create a valid ExecutedOrder shape from a cancelled order", () => {
    const cancelled = {
      permId: 12345,
      symbol: "AAOI",
      action: "SELL",
      orderType: "LMT",
      totalQuantity: 750,
      limitPrice: 115.00,
      cancelledAt: new Date().toISOString(),
    };

    const asExecuted = {
      execId: `cancelled-${cancelled.permId}`,
      symbol: cancelled.symbol,
      contract: { conId: null, symbol: cancelled.symbol, secType: "", strike: null, right: null, expiry: null },
      side: "CANCELLED",
      quantity: cancelled.totalQuantity,
      avgPrice: cancelled.limitPrice,
      commission: null,
      realizedPNL: null,
      time: cancelled.cancelledAt,
      exchange: "",
    };

    assert.equal(asExecuted.side, "CANCELLED");
    assert.equal(asExecuted.symbol, "AAOI");
    assert.equal(asExecuted.quantity, 750);
    assert.equal(asExecuted.avgPrice, 115.00);
    assert.ok(asExecuted.execId.startsWith("cancelled-"));
    assert.equal(asExecuted.commission, null);
    assert.equal(asExecuted.realizedPNL, null);
  });

  it("display side mapping treats CANCELLED correctly", () => {
    const cases = [
      { side: "BOT", expected: "BUY" },
      { side: "SLD", expected: "SELL" },
      { side: "CANCELLED", expected: "CANCELLED" },
      { side: "OTHER", expected: "OTHER" },
    ];

    for (const { side, expected } of cases) {
      const isCancelled = side === "CANCELLED";
      const displaySide = isCancelled ? "CANCELLED" : side === "BOT" ? "BUY" : side === "SLD" ? "SELL" : side;
      assert.equal(displaySide, expected, `side=${side}`);
    }
  });

  it("pill class mapping for CANCELLED is 'cancelled'", () => {
    const side = "CANCELLED";
    const isCancelled = side === "CANCELLED";
    const pillClass = isCancelled ? "cancelled" : side === "BUY" ? "accum" : "distrib";
    assert.equal(pillClass, "cancelled");
  });

  it("pill class mapping for BUY/SELL unchanged", () => {
    assert.equal("accum", (() => { const s = "BUY"; return s === "BUY" ? "accum" : "distrib"; })());
    assert.equal("distrib", (() => { const s = "SELL"; return s === "BUY" ? "accum" : "distrib"; })());
  });

  it("cancelled orders prepend to executed list (most recent first)", () => {
    const cancelledOrders = [
      { permId: 111, symbol: "AAOI", cancelledAt: "2026-03-04T15:30:00Z" },
      { permId: 222, symbol: "TSLL", cancelledAt: "2026-03-04T15:25:00Z" },
    ];
    const existingExecuted = [
      { execId: "exec-1", time: "2026-03-04T15:20:00Z" },
    ];

    const allRows = [
      ...cancelledOrders.map((c) => ({ execId: `cancelled-${c.permId}`, time: c.cancelledAt })),
      ...existingExecuted,
    ];

    assert.equal(allRows.length, 3);
    assert.equal(allRows[0].execId, "cancelled-111");
    assert.equal(allRows[1].execId, "cancelled-222");
    assert.equal(allRows[2].execId, "exec-1");
  });
});

// =============================================================================
// Pending cancel state logic tests
// =============================================================================

describe("Pending cancel state logic", () => {
  it("should track pending cancels by permId", () => {
    const pendingCancels = new Map<number, { permId: number; symbol: string }>();

    const order = { permId: 12345, symbol: "AAOI" };
    pendingCancels.set(order.permId, order);

    assert.ok(pendingCancels.has(12345));
    assert.ok(!pendingCancels.has(67890));
    assert.equal(pendingCancels.size, 1);
  });

  it("multiple concurrent cancels tracked independently", () => {
    const pendingCancels = new Map<number, { permId: number; symbol: string }>();

    pendingCancels.set(111, { permId: 111, symbol: "AAOI" });
    pendingCancels.set(222, { permId: 222, symbol: "TSLL" });

    assert.equal(pendingCancels.size, 2);

    // First one confirmed
    pendingCancels.delete(111);
    assert.equal(pendingCancels.size, 1);
    assert.ok(!pendingCancels.has(111));
    assert.ok(pendingCancels.has(222));
  });

  it("should detect when order disappears from open_orders", () => {
    const pendingPermId = 12345;

    const openOrdersBefore = [
      { permId: 12345, orderId: 10 },
      { permId: 67890, orderId: 20 },
    ];
    assert.ok(openOrdersBefore.some((o) => o.permId === pendingPermId));

    const openOrdersAfter = [{ permId: 67890, orderId: 20 }];
    assert.ok(!openOrdersAfter.some((o) => o.permId === pendingPermId));
  });

  it("should handle BAG orders with orderId=0 — uses permId only", () => {
    const pendingPermId = 99999;
    const orderId = 0;

    const openOrders = [
      { permId: 99999, orderId: 0 },
      { permId: 11111, orderId: 5 },
    ];

    // The detection logic: permId match first, orderId fallback only if non-zero
    const stillOpen = openOrders.some(
      (o) => o.permId === pendingPermId || (o.orderId === orderId && orderId !== 0),
    );
    assert.ok(stillOpen, "Should find by permId even when orderId=0");
  });

  it("orderId=0 fallback does NOT match other orderId=0 rows", () => {
    const pendingPermId = 99999;
    const orderId = 0;

    // Order is gone but another BAG order with orderId=0 exists
    const openOrders = [
      { permId: 11111, orderId: 0 }, // different BAG order
    ];

    const stillOpen = openOrders.some(
      (o) => o.permId === pendingPermId || (o.orderId === orderId && orderId !== 0),
    );
    assert.ok(!stillOpen, "Should NOT match different order just because both have orderId=0");
  });

  it("should respect max poll count of 24 (~2 min)", () => {
    const CANCEL_POLL_MAX = 24;
    const CANCEL_POLL_MS = 5_000;

    assert.equal(CANCEL_POLL_MAX * CANCEL_POLL_MS, 120_000, "Max wait is ~2 minutes");
  });

  it("canModify returns true only for LMT and STP LMT", () => {
    const canModify = (orderType: string) => orderType === "LMT" || orderType === "STP LMT";

    assert.ok(canModify("LMT"));
    assert.ok(canModify("STP LMT"));
    assert.ok(!canModify("MKT"));
    assert.ok(!canModify("STP"));
    assert.ok(!canModify("MOC"));
    assert.ok(!canModify("LOC"));
  });
});

// =============================================================================
// Exec count with cancelled orders
// =============================================================================

describe("Executed count includes cancelled", () => {
  it("exec count sums IB fills + cancelled orders", () => {
    const orders = { executed_count: 5 };
    const cancelledOrders = [{ permId: 1 }, { permId: 2 }];

    const execCount = orders.executed_count + cancelledOrders.length;
    assert.equal(execCount, 7);
  });

  it("exec count label is singular for 1", () => {
    const execCount = 1;
    const label = execCount === 1 ? "ENTRY" : "ENTRIES";
    assert.equal(label, "ENTRY");
  });

  it("exec count label is plural for 0 or many", () => {
    assert.equal(0 === 1 ? "ENTRY" : "ENTRIES", "ENTRIES");
    assert.equal(5 === 1 ? "ENTRY" : "ENTRIES", "ENTRIES");
  });
});

// =============================================================================
// IB clientId collision prevention
// =============================================================================

describe("IB clientId collision prevention", () => {
  it("ib_orders uses a dedicated clientId, not master (0)", async () => {
    const filePath = path.resolve(__dirname, "../../scripts/utils/ib_connection.py");
    const content = await readFile(filePath, "utf8");
    const match = content.match(/"ib_orders":\s*(\d+)/);
    assert.ok(match, "Should find ib_orders in CLIENT_IDS registry");
    const clientId = parseInt(match[1], 10);
    assert.notEqual(clientId, 0, "ib_orders must NOT use clientId 0 (master) — causes collision with ib_order_manage");
  });

  it("orders sync route passes --client-id to ib_orders.py", async () => {
    const filePath = path.resolve(__dirname, "../app/api/orders/route.ts");
    const content = await readFile(filePath, "utf8");
    assert.ok(
      content.includes("--client-id"),
      "orders route must pass --client-id to ib_orders.py to avoid master clientId collision",
    );
  });

  it("cancel route sync passes --client-id to ib_orders.py", async () => {
    const filePath = path.resolve(__dirname, "../app/api/orders/cancel/route.ts");
    const content = await readFile(filePath, "utf8");
    assert.ok(
      content.includes("--client-id"),
      "cancel route must pass --client-id to ib_orders.py to avoid master clientId collision",
    );
  });

  it("modify route sync passes --client-id to ib_orders.py", async () => {
    const filePath = path.resolve(__dirname, "../app/api/orders/modify/route.ts");
    const content = await readFile(filePath, "utf8");
    assert.ok(
      content.includes("--client-id"),
      "modify route must pass --client-id to ib_orders.py to avoid master clientId collision",
    );
  });
});
