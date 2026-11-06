/**
 * Bug guard: /api/scanner must return Cache-Control: no-store on every
 * response so browsers and intermediaries never serve a stale snapshot.
 */

import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

const mockExecute = vi.hoisted(() => vi.fn());
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => ({ execute: mockExecute }) }));

vi.mock("@/lib/radonApi", () => ({
  radonFetch: vi.fn().mockResolvedValue({
    scan_time: "2026-05-09T02:04:09.999Z",
    tickers_scanned: 0,
    signals_found: 0,
    top_signals: [],
  }),
}));


const validCachePayload = JSON.stringify({
  scan_time: "2026-05-09T02:04:09.999Z",
  tickers_scanned: 0,
  signals_found: 0,
  top_signals: [],
});

function expectNoStore(res: Response): void {
  const cc = res.headers.get("Cache-Control") ?? "";
  expect(cc.toLowerCase()).toContain("no-store");
}

describe("/api/scanner — Cache-Control: no-store", () => {
  beforeEach(() => {
    vi.resetModules();
    mockExecute.mockResolvedValue({
      rows: [{ scan_time: new Date().toISOString(), payload: validCachePayload }],
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("GET sets no-store when serving a Turso snapshot", async () => {
    const { GET } = await import("../app/api/scanner/route");
    const res = await GET();
    expectNoStore(res);
  });

  it("GET sets no-store on the empty-fallback envelope when Turso is empty and nothing is remembered", async () => {
    mockExecute.mockResolvedValue({ rows: [] });

    const { GET } = await import("../app/api/scanner/route");
    const res = await GET();
    expectNoStore(res);
  });

  it("POST sets no-store on the FastAPI-passthrough response", async () => {
    const { POST } = await import("../app/api/scanner/route");
    const res = await POST();
    expectNoStore(res);
  });
});
