/**
 * @vitest-environment node
 *
 * PERF-048: GET /api/quant/[strategy] must cost ONE Turso HTTP pipeline for
 * the three independent snapshot reads (quant_runs + quant_targets +
 * quant_orders).
 *
 * The QUANT tab polls this hourly. A cache miss still issued three
 * sequential db.execute calls (three /v2/pipeline POSTs) even though the
 * SELECTs do not depend on each other. FastAPI and the Next.js orders /
 * portfolio snapshot paths already batch sibling reads (PERF-002, PERF-016,
 * PERF-025).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  execute: vi.fn(),
  batch: vi.fn(),
  resetDb: vi.fn(),
}));

vi.mock("@/lib/db", () => ({
  resetDb: mocks.resetDb,
  getDb: () => ({ execute: mocks.execute, batch: mocks.batch }),
}));

const RUN_AT = "2026-10-30T22:00:00Z";
const runRow = {
  run_at: RUN_AT,
  since: "2007-01-03",
  until: "2026-10-30",
  n_sessions: 3,
  n_rebalances: 1,
  payload: JSON.stringify({
    since: "2007-01-03",
    until: "2026-10-30",
    n_sessions: 3,
    n_rebalances: 1,
    cost_bps: 5,
    universe: ["SPY", "TLT"],
    dates: ["2026-10-28", "2026-10-29", "2026-10-30"],
    equity: [100000, 101000, 99000],
    final_weights: { SPY: 0.6, TLT: 0.4 },
    metrics: { sharpe: 0.67, max_drawdown: -0.204, annualized_return: 0.055, ann_vol: 0.082 },
  }),
};
const targetRow = {
  as_of: "2026-10-30",
  symbol: "SPY",
  weight: 0.6,
  notional: 60000,
  recorded_at: "2026-10-30T21:00:00Z",
  close: 505.5,
  rebalance_today: 1,
};
const orderRow = {
  as_of: "2026-10-30",
  symbol: "SPY",
  action: "BUY",
  quantity: 20,
  limit_price: 101.25,
  status: "planned",
  message: "dry run",
  placed_at: "2026-10-31T13:35:00Z",
};

function resultForSql(sql: string) {
  if (/FROM\s+quant_runs/i.test(sql)) return { rows: [runRow] };
  if (/FROM\s+quant_targets/i.test(sql)) return { rows: [targetRow] };
  if (/FROM\s+quant_orders/i.test(sql)) return { rows: [orderRow] };
  throw new Error(`unexpected sql: ${sql}`);
}

beforeEach(async () => {
  vi.resetModules();
  mocks.execute.mockReset();
  mocks.batch.mockReset();
  mocks.resetDb.mockReset();
  mocks.execute.mockImplementation(async (stmt: { sql: string }) => resultForSql(stmt.sql));
  mocks.batch.mockImplementation(async (stmts: Array<{ sql: string }>) =>
    stmts.map((stmt) => resultForSql(stmt.sql)),
  );
  const { __resetDbFirstReadMemoryForTests } = await import("@/lib/dbFirstRead");
  __resetDbFirstReadMemoryForTests();
});

describe("PERF-048 quant pipeline budget", () => {
  it("loads run, targets, and orders in one batch, not three executes", async () => {
    const { GET } = await import("../app/api/quant/[strategy]/route");
    const res = await GET(new Request("http://localhost/api/quant/inverse_vol"), {
      params: Promise.resolve({ strategy: "inverse_vol" }),
    });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.missing).toBe(false);
    expect(body.run.run_at).toBe(RUN_AT);
    expect(body.targets.rows).toEqual([
      { symbol: "SPY", weight: 0.6, notional: 60000, close: 505.5 },
    ]);
    expect(body.orders.rows).toEqual([
      {
        symbol: "SPY",
        action: "BUY",
        quantity: 20,
        limit_price: 101.25,
        status: "planned",
        message: "dry run",
      },
    ]);

    const quantExecutes = mocks.execute.mock.calls.filter(([stmt]) =>
      /FROM\s+quant_(runs|targets|orders)/i.test(String(stmt?.sql ?? stmt)),
    );
    expect(quantExecutes, "sibling quant SELECTs must not each open a pipeline").toHaveLength(0);
    expect(mocks.batch).toHaveBeenCalledTimes(1);
    expect(mocks.batch.mock.calls[0][0]).toHaveLength(3);
    const batched = mocks.batch.mock.calls[0][0] as Array<{ sql: string }>;
    expect(batched[0].sql).toMatch(/FROM\s+quant_runs/i);
    expect(batched[1].sql).toMatch(/FROM\s+quant_targets/i);
    expect(batched[2].sql).toMatch(/FROM\s+quant_orders/i);
    expect(mocks.batch.mock.calls[0][1]).toBe("read");
  });
});
