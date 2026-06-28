/**
 * Bug guard: /api/blotter must return Cache-Control: no-store on every
 * response so browsers and intermediaries never serve a stale snapshot.
 */

import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

const mockExecute = vi.fn().mockResolvedValue({ rows: [] });
vi.mock("@/lib/db", () => ({ resetDb: () => {},
  getDb: () => ({
    execute: mockExecute,
  }),
}));

vi.mock("@/lib/radonApi", () => ({
  radonFetch: vi.fn().mockResolvedValue({
    as_of: "2026-05-09",
    summary: { closed_trades: 0, open_trades: 0, total_commissions: 0, realized_pnl: 0 },
    closed_trades: [],
    open_trades: [],
  }),
}));

function expectNoStore(res: Response): void {
  const cc = res.headers.get("Cache-Control") ?? "";
  expect(cc.toLowerCase()).toContain("no-store");
}

describe("/api/blotter — Cache-Control: no-store", () => {
  beforeEach(() => {
    vi.resetModules();
    mockExecute.mockReset();
    mockExecute.mockResolvedValue({ rows: [] });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("GET sets no-store when serving the Turso-derived journal payload", async () => {
    mockExecute.mockResolvedValue({
      rows: [
        {
          payload: JSON.stringify({
            id: 1,
            date: "2026-05-09",
            ticker: "AAPL",
            action: "SELL_OPTION",
            fill_price: 5,
            total_cost: 500,
            contracts: 1,
            commission: 2.5,
            realized_pnl: 500,
            ib_exec_id: "AAPL-1",
          }),
          filled_at: "2026-05-09T15:00:00Z",
        },
      ],
    });
    const { GET } = await import("../app/api/blotter/route");
    const res = await GET();
    expectNoStore(res);
  });

  it("GET sets no-store on the empty-payload envelope when the journal is empty", async () => {
    const { GET } = await import("../app/api/blotter/route");
    const res = await GET();
    expectNoStore(res);
  });

  it("POST sets no-store on the FastAPI-passthrough response", async () => {
    const { POST } = await import("../app/api/blotter/route");
    const res = await POST();
    expectNoStore(res);
  });
});
