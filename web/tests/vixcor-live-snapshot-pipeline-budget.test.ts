/**
 * @vitest-environment node
 *
 * PERF-062: GET /api/vixcor must cost ONE Turso HTTP pipeline for the official
 * snapshot plus the vixcor:live provisional row.
 *
 * useVixcor polls this every 5 minutes to match radon-vixcor.timer. A cache
 * miss still issued two sequential db.execute calls (two /v2/pipeline POSTs)
 * even though the SELECTs are independent. The same sibling-read batch already
 * exists for orders (PERF-016), portfolio entry-dates (PERF-025), and quant
 * (PERF-048).
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
  getPoolStats: () => null,
}));

const SCAN_TIME = new Date().toISOString();
const officialRow = {
  date: "2026-08-14",
  vix_close: 14.25,
  cor3m_close: 12.34,
  corr20: 0.014969,
  episode: true,
};
const liveRow = {
  date: "2026-08-17",
  vix_close: 22.1,
  cor3m_close: 13.2,
  corr20: 0.1834,
  episode: true,
};
const liveCurrent = {
  ...liveRow,
  change_1d: 0.1684,
  percentile: 0.03,
  regime: "DECOUPLED",
  vix_cov_20d: 0.08,
  episode: null,
};

const officialPayload = {
  scan_time: SCAN_TIME,
  status: "ok",
  as_of: "2026-08-14",
  parent_as_of: "2026-08-14",
  expected_session: "2026-08-14",
  lag_sessions: 0,
  count: 1,
  series: [officialRow],
  episodes: [],
  current: officialRow,
  stats: null,
  forward_stats: null,
};

const livePayload = {
  scan_time: SCAN_TIME,
  row: liveRow,
  current: liveCurrent,
  source: "ib",
  quote_time: null,
};

function resultForSql(sql: string) {
  if (/service = 'vixcor'/i.test(sql) && !/vixcor:live/i.test(sql)) {
    return { rows: [{ scan_time: SCAN_TIME, payload: JSON.stringify(officialPayload) }] };
  }
  if (/vixcor:live/i.test(sql) || /service = \?/i.test(sql)) {
    return { rows: [{ payload: JSON.stringify(livePayload) }] };
  }
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

describe("PERF-062 vixcor live-snapshot pipeline budget", () => {
  it("loads official and live snapshots in one batch, not two executes", async () => {
    const { GET } = await import("../app/api/vixcor/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.as_of).toBe("2026-08-17");
    expect(body.current).toEqual(liveCurrent);
    expect(body.provisional).toEqual({
      source: "ib",
      quote_time: null,
      scan_time: SCAN_TIME,
    });

    const snapshotExecutes = mocks.execute.mock.calls.filter(([stmt]) =>
      /scan_snapshots/i.test(String(stmt?.sql ?? stmt)),
    );
    expect(snapshotExecutes, "sibling snapshot SELECTs must not each open a pipeline").toHaveLength(0);
    expect(mocks.batch).toHaveBeenCalledTimes(1);
    expect(mocks.batch.mock.calls[0][0]).toHaveLength(2);
    const batched = mocks.batch.mock.calls[0][0] as Array<{ sql: string; args?: unknown[] }>;
    expect(batched[0].sql).toMatch(/scan_snapshots/i);
    expect(batched[0].sql).toMatch(/service = 'vixcor'/i);
    expect(batched[1].sql).toMatch(/scan_snapshots/i);
    expect(batched[1].args).toEqual(["vixcor:live"]);
    expect(mocks.batch.mock.calls[0][1]).toBe("read");
  });
});
