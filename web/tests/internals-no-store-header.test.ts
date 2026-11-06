/**
 * Bug guard: /api/internals must return Cache-Control: no-store on every
 * response so browsers and intermediaries never serve a stale snapshot.
 */

import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

vi.mock("fs/promises", () => ({
  writeFile: vi.fn().mockResolvedValue(undefined),
  mkdir: vi.fn().mockResolvedValue(undefined),
}));

const mockExecute = vi.hoisted(() => vi.fn());
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => ({ execute: mockExecute }) }));

vi.mock("@/lib/radonApi", () => ({
  radonFetch: vi.fn().mockResolvedValue({}),
}));


function expectNoStore(res: Response): void {
  const cc = res.headers.get("Cache-Control") ?? "";
  expect(cc.toLowerCase()).toContain("no-store");
}

const validCriPayload = JSON.stringify({
  scan_time: "2026-05-08T20:00:00Z",
  date: "2026-05-08",
  vix: 15,
  vvix: 80,
  spy: 500,
  cri: { score: 1, level: "LOW" },
  cta: { realized_vol: 10, exposure_pct: 200, forced_reduction_pct: 0, est_selling_bn: 0 },
  history: [],
  spy_closes: [],
});

describe("/api/internals — Cache-Control: no-store", () => {
  beforeEach(() => {
    vi.resetModules();
    mockExecute.mockImplementation(async ({ sql }: { sql: string }) => ({
      rows: sql.includes("cri_snapshots")
        ? [{ taken_at: new Date().toISOString(), payload: validCriPayload }]
        : [],
    }));
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("GET sets no-store on the success path", async () => {
    const { GET } = await import("../app/api/internals/route");
    const res = await GET();
    expectNoStore(res);
  });

  it("POST sets no-store on the success path", async () => {
    const { POST } = await import("../app/api/internals/route");
    const res = await POST();
    expectNoStore(res);
  });
});
