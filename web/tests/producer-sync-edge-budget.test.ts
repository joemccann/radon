import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * POST /api/portfolio and POST /api/orders sit behind Caddy's 30s
 * response_header_timeout. They used to wait 35s on FastAPI, so a slow IB sync
 * reached the browser as Caddy's raw 504 and the route's Turso-snapshot
 * fallback ran for a client that was already gone (54 edge 504s in 48h, every
 * one at exactly 30.0s). Asserted at the wire: the real radonFetch, a stubbed
 * global fetch, the full upstream URL, method and the timeout it arms.
 */

const RADON_API = "http://localhost:8321";
vi.stubEnv("RADON_API_URL", RADON_API);

const mockExecute = vi.fn();
vi.mock("@/lib/db", () => ({ getDb: () => ({ execute: mockExecute }), resetDb: () => {} }));

const mockReadOrdersSnapshotFromDb = vi.fn();
vi.mock("@/lib/orders/readOrdersFromDb", () => ({
  readOrdersSnapshotFromDb: mockReadOrdersSnapshotFromDb,
}));

const mockFetch = vi.fn();

function portfolio(lastSync: string) {
  return {
    bankroll: 100_000,
    peak_value: 100_000,
    last_sync: lastSync,
    positions: [],
    total_deployed_pct: 0,
    total_deployed_dollars: 0,
    remaining_capacity_pct: 100,
    position_count: 0,
    defined_risk_count: 0,
    undefined_risk_count: 0,
    avg_kelly_optimal: null,
  };
}

function storePortfolio(snapshot: Record<string, unknown>) {
  mockExecute.mockImplementation(async ({ sql }: { sql: string }) => {
    if (/FROM\s+portfolio_snapshots/i.test(sql)) {
      return { rows: [{ taken_at: snapshot.last_sync, payload: JSON.stringify(snapshot) }] };
    }
    return { rows: [] };
  });
}

function timeoutError(): DOMException {
  return new DOMException("The operation was aborted due to timeout", "TimeoutError");
}

function upstreamCall() {
  const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
  return { url, method: init.method };
}

let timeoutSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.resetModules();
  mockFetch.mockReset();
  mockExecute.mockReset();
  mockExecute.mockResolvedValue({ rows: [] });
  mockReadOrdersSnapshotFromDb.mockReset();
  vi.stubGlobal("fetch", mockFetch);
  timeoutSpy = vi.spyOn(AbortSignal, "timeout");
});

afterEach(() => {
  timeoutSpy.mockRestore();
  vi.unstubAllGlobals();
});

describe("POST /api/portfolio edge budget", () => {
  it("arms the FastAPI sync with a wait that leaves room for the fallback under the edge", async () => {
    const { PRODUCER_SYNC_WAIT_MS } = await import("../lib/edgeBudget");
    mockFetch.mockResolvedValue(new Response(JSON.stringify(portfolio("2026-10-08T15:00:00Z")), { status: 200 }));

    const { POST } = await import("../app/api/portfolio/route");
    const response = await POST();

    expect(response.status).toBe(200);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(upstreamCall()).toEqual({ url: `${RADON_API}/portfolio/sync`, method: "POST" });
    expect(timeoutSpy).toHaveBeenCalledWith(PRODUCER_SYNC_WAIT_MS);
    expect(PRODUCER_SYNC_WAIT_MS).toBeLessThanOrEqual(20_000);
  });

  it("serves the Turso snapshot as sync-pending when the wait elapses", async () => {
    storePortfolio(portfolio("2026-10-08T14:59:00Z"));
    mockFetch.mockRejectedValue(timeoutError());

    const { POST } = await import("../app/api/portfolio/route");
    const response = await POST();
    const body = await response.json();

    expect(upstreamCall()).toEqual({ url: `${RADON_API}/portfolio/sync`, method: "POST" });
    expect(response.status).toBe(200);
    expect(body.last_sync).toBe("2026-10-08T14:59:00Z");
    expect(response.headers.get("X-Sync-Pending")).toBe("1");
    expect(response.headers.get("X-Sync-Warning")).toBe(
      "IB sync still running - serving latest Turso snapshot",
    );
  });

  it("keeps the failure warning, without sync-pending, when FastAPI answers with an error", async () => {
    storePortfolio(portfolio("2026-10-08T14:59:00Z"));
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ detail: "ib_sync failed" }), { status: 502 }));

    const { POST } = await import("../app/api/portfolio/route");
    const response = await POST();

    expect(response.status).toBe(200);
    expect(response.headers.get("X-Sync-Pending")).toBeNull();
    expect(response.headers.get("X-Sync-Warning")).toBe("IB sync failed - serving latest Turso snapshot");
  });
});

describe("POST /api/orders edge budget", () => {
  it("arms the FastAPI refresh with the same edge-safe wait", async () => {
    const { PRODUCER_SYNC_WAIT_MS } = await import("../lib/edgeBudget");
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    mockReadOrdersSnapshotFromDb.mockResolvedValue({ last_sync: "2026-10-08T15:00:00Z", open_orders: [] });

    const { POST } = await import("../app/api/orders/route");
    const response = await POST();

    expect(response.status).toBe(200);
    expect(upstreamCall()).toEqual({ url: `${RADON_API}/orders/refresh`, method: "POST" });
    expect(timeoutSpy).toHaveBeenCalledWith(PRODUCER_SYNC_WAIT_MS);
    const headers = new Headers((mockFetch.mock.calls[0][1] as RequestInit).headers);
    expect(headers.get("X-Radon-Orders-Fresh")).toBeNull();
  });

  it("serves the Turso orders snapshot as sync-pending when the wait elapses", async () => {
    mockFetch.mockRejectedValue(timeoutError());
    mockReadOrdersSnapshotFromDb.mockResolvedValue({ last_sync: "2026-10-08T14:59:00Z", open_orders: [] });

    const { POST } = await import("../app/api/orders/route");
    const response = await POST();

    expect(response.status).toBe(200);
    expect(response.headers.get("X-Sync-Pending")).toBe("1");
    expect(response.headers.get("X-Sync-Warning")).toBe(
      "IB sync still running - serving latest Turso snapshot",
    );
  });
});
