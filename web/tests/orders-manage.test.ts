import { describe, it, expect, beforeAll } from "vitest";
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
 * 1. Cancel API route -- input validation (no IB connection needed)
 * 2. Modify API route -- input validation (no IB connection needed)
 * 3. IB clientId collision prevention (structural checks)
 * 4. Cross-client modify fix (Error 103)
 */

// =============================================================================
// Cancel API route validation tests
// =============================================================================

describe("POST /api/orders/cancel validation", () => {
  let cancelPOST: (req: Request) => Promise<Response>;

  beforeAll(async () => {
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
    expect(res.status).toBe(400);
    const body = await res.json();
    expect((body as { error: string }).error.includes("orderId")).toBeTruthy();
  });

  it("rejects orderId=0 and permId=0", async () => {
    const req = new NextRequest("http://localhost/api/orders/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orderId: 0, permId: 0 }),
    });

    const res = await cancelPOST(req);
    expect(res.status).toBe(400);
  });
});

// =============================================================================
// Modify API route validation tests
// =============================================================================

describe("POST /api/orders/modify validation", () => {
  let modifyPOST: (req: Request) => Promise<Response>;

  beforeAll(async () => {
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
    expect(res.status).toBe(400);
  });

  it("rejects missing newPrice", async () => {
    const req = new NextRequest("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permId: 12345 }),
    });

    const res = await modifyPOST(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect((body as { error: string }).error.includes("newPrice")).toBeTruthy();
  });

  it("rejects newPrice of zero", async () => {
    const req = new NextRequest("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permId: 12345, newPrice: 0 }),
    });

    const res = await modifyPOST(req);
    expect(res.status).toBe(400);
  });

  it("rejects negative newPrice", async () => {
    const req = new NextRequest("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permId: 12345, newPrice: -5 }),
    });

    const res = await modifyPOST(req);
    expect(res.status).toBe(400);
  });
});

// =============================================================================
// IB clientId collision prevention
// =============================================================================

describe("IB clientId collision prevention", () => {
  it("ib_orders uses a dedicated clientId, not master (0)", async () => {
    const filePath = path.resolve(__dirname, "../../scripts/clients/ib_client.py");
    const content = await readFile(filePath, "utf8");
    const match = content.match(/"ib_orders":\s*(\d+)/);
    expect(match).toBeTruthy();
    const clientId = parseInt(match![1], 10);
    expect(clientId).not.toBe(0);
  });

  it("orders sync route passes clientId via typed wrapper", async () => {
    const filePath = path.resolve(__dirname, "../app/api/orders/route.ts");
    const content = await readFile(filePath, "utf8");
    expect(content.includes("radonFetch")).toBeTruthy();
    expect(content.includes('"/orders/refresh"')).toBeTruthy();
  });

  it("cancel route passes clientId via typed wrapper", async () => {
    const filePath = path.resolve(__dirname, "../app/api/orders/cancel/route.ts");
    const content = await readFile(filePath, "utf8");
    expect(content.includes("radonFetch")).toBeTruthy();
    expect(content.includes('"/orders/cancel"')).toBeTruthy();
  });

  it("modify route passes clientId via typed wrapper", async () => {
    const filePath = path.resolve(__dirname, "../app/api/orders/modify/route.ts");
    const content = await readFile(filePath, "utf8");
    expect(content.includes("radonFetch")).toBeTruthy();
    expect(content.includes('"/orders/modify"')).toBeTruthy();
  });
});

// =============================================================================
// Cross-client modify fix -- reconnect as original clientId (Error 103 fix)
// =============================================================================

describe("Cross-client modify fix (Error 103)", () => {
  let scriptContent: string;

  beforeAll(async () => {
    const filePath = path.resolve(__dirname, "../../scripts/ib_order_manage.py");
    scriptContent = await readFile(filePath, "utf8");
  });

  it("modify_order accepts host and port parameters", () => {
    // The function signature must include host and port so it can reconnect
    const sigMatch = scriptContent.match(/def modify_order\(([^)]+)\)/);
    expect(sigMatch).toBeTruthy();
    const params = sigMatch![1];
    expect(params.includes("host")).toBeTruthy();
    expect(params.includes("port")).toBeTruthy();
  });

  it("modify_order reconnects as original clientId", () => {
    // Must read trade.order.clientId and reconnect if different
    expect(
      scriptContent.includes("trade.order.clientId") || scriptContent.includes("original_client_id"),
    ).toBeTruthy();
    expect(
      scriptContent.includes("client.disconnect()"),
    ).toBeTruthy();
  });

  it("modify_order detects IB error events", () => {
    // Must register an errorEvent listener to catch Error 103/201/202
    expect(scriptContent.includes("errorEvent")).toBeTruthy();
  });
});
