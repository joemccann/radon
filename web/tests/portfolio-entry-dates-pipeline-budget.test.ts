/**
 * @vitest-environment node
 *
 * PERF-025: GET /api/portfolio?include=entry-dates must cost ONE Turso HTTP
 * pipeline for the two journal-derived maps (trade_log_dates +
 * contract_open_dates).
 *
 * WorkspaceShell polls this every 2s on /orders during RTH. The maps share a
 * 60s cache, so a miss still issued two dbExecute calls (two /v2/pipeline
 * POSTs) even though the SELECTs are independent. FastAPI already batches
 * sibling snapshot reads (PERF-002); the Next.js share-card path did not.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  execute: vi.fn(),
  batch: vi.fn(),
}));

vi.mock("@/lib/db", () => ({
  resetDb: () => {},
  getDb: () => ({ execute: mocks.execute, batch: mocks.batch }),
}));
vi.mock("@/lib/radonApi", () => ({ radonFetch: vi.fn() }));

const SNAPSHOT = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-10-23T20:00:00Z",
  positions: [],
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
};

const tradeRow = { ticker: "INTC", date: "2026-05-05" };
const contractRow = {
  ticker: "MSFT",
  expiry: "20260717",
  opt_right: "C",
  strike: 410,
  action: "SELL_TO_OPEN",
  contracts: 25,
  date: "2026-07-02",
};

function resultForSql(sql: string) {
  if (/FROM\s+portfolio_snapshots/i.test(sql)) {
    return {
      rows: [{ taken_at: SNAPSHOT.last_sync, payload: JSON.stringify(SNAPSHOT) }],
    };
  }
  if (/FROM\s+journal/i.test(sql)) {
    if (/opt_right/i.test(sql)) return { rows: [contractRow] };
    return { rows: [tradeRow] };
  }
  throw new Error(`unexpected sql: ${sql}`);
}

beforeEach(() => {
  vi.resetModules();
  mocks.execute.mockReset();
  mocks.batch.mockReset();
  mocks.execute.mockImplementation(async (stmt: { sql: string }) => resultForSql(stmt.sql));
  mocks.batch.mockImplementation(async (stmts: Array<{ sql: string }>) =>
    stmts.map((stmt) => resultForSql(stmt.sql)),
  );
});

describe("PERF-025 portfolio entry-dates pipeline budget", () => {
  it("loads both journal maps in one batch, not two executes", async () => {
    const { GET } = await import("../app/api/portfolio/route");
    const res = await GET(
      new Request("http://localhost/api/portfolio?include=entry-dates"),
    );
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.trade_log_dates).toEqual({ INTC: "2026-05-05" });
    expect(body.contract_open_dates).toEqual({ "MSFT|20260717|C|410": "2026-07-02" });

    const journalExecutes = mocks.execute.mock.calls.filter(([stmt]) =>
      /FROM\s+journal/i.test(String(stmt?.sql ?? stmt)),
    );
    expect(journalExecutes).toHaveLength(0);
    expect(mocks.batch).toHaveBeenCalledTimes(1);
    expect(mocks.batch.mock.calls[0][0]).toHaveLength(2);
    const batched = mocks.batch.mock.calls[0][0] as Array<{ sql: string }>;
    expect(batched[0].sql).toMatch(/FROM\s+journal/i);
    expect(batched[0].sql).toMatch(/GROUP BY/i);
    expect(batched[1].sql).toMatch(/FROM\s+journal/i);
    expect(batched[1].sql).toMatch(/opt_right/i);
    expect(mocks.batch.mock.calls[0][1]).toBe("read");
  });
});
