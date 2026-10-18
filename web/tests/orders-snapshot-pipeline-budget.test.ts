/**
 * @vitest-environment node
 *
 * PERF-016: GET /api/orders must cost ONE Turso HTTP pipeline for the
 * open + executed snapshot reads.
 *
 * FastAPI already batches these two SELECTs (PERF-002). The Next.js poll
 * path still fired two dbExecute calls (two /v2/pipeline POSTs) even though
 * they are independent. WorkspaceShell polls this during RTH.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  execute: vi.fn(),
  batch: vi.fn(),
  syncDb: vi.fn(async () => {}),
  resetDb: vi.fn(),
}));

vi.mock("@/lib/db", () => ({
  getDb: () => ({ execute: mocks.execute, batch: mocks.batch }),
  syncDb: mocks.syncDb,
  resetDb: mocks.resetDb,
  getPoolStats: () => null,
}));

import { readOrdersFromDb } from "@/lib/orders/readOrdersFromDb";

const todayEt = new Date().toLocaleDateString("sv", { timeZone: "America/New_York" });
const todayFillIso = `${todayEt}T12:00:00-04:00`;

const openRow = {
  payload: JSON.stringify({
    orderId: 1,
    permId: 1,
    symbol: "TSLA",
    contract: { conId: 1, symbol: "TSLA", secType: "STK" },
    action: "BUY",
    orderType: "LMT",
    totalQuantity: 1,
    limitPrice: 5,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: 1,
    avgFillPrice: null,
    tif: "GTC",
  }),
  updated_at: "2026-10-17T13:30:00Z",
};

const execRow = {
  payload: JSON.stringify({
    execId: "e1",
    symbol: "TSLA",
    contract: { conId: 1, symbol: "TSLA", secType: "STK" },
    side: "BOT",
    quantity: 1,
    avgPrice: 5.25,
    commission: 0.65,
    realizedPNL: null,
    time: todayFillIso,
    exchange: "SMART",
  }),
  fill_time: todayFillIso,
};

function resultForSql(sql: string) {
  if (/FROM\s+open_orders/i.test(sql)) return { rows: [openRow] };
  if (/FROM\s+executed_orders/i.test(sql)) return { rows: [execRow] };
  throw new Error(`unexpected sql: ${sql}`);
}

beforeEach(() => {
  mocks.execute.mockReset();
  mocks.batch.mockReset();
  mocks.syncDb.mockClear();
  mocks.resetDb.mockClear();
  mocks.execute.mockImplementation(async (stmt: { sql: string }) => resultForSql(stmt.sql));
  mocks.batch.mockImplementation(async (stmts: Array<{ sql: string }>) =>
    stmts.map((stmt) => resultForSql(stmt.sql)),
  );
});

describe("PERF-016 orders snapshot pipeline budget", () => {
  it("loads open and executed orders in one batch, not two executes", async () => {
    const snapshot = await readOrdersFromDb();

    expect(snapshot?.open_count).toBe(1);
    expect(snapshot?.executed_count).toBe(1);
    expect(snapshot?.open_orders[0].permId).toBe(1);
    expect(snapshot?.executed_orders[0].execId).toBe("e1");

    const snapshotExecutes = mocks.execute.mock.calls.filter(([stmt]) =>
      /FROM\s+(open_orders|executed_orders)/i.test(String(stmt?.sql ?? stmt)),
    );
    expect(snapshotExecutes).toHaveLength(0);
    expect(mocks.batch).toHaveBeenCalledTimes(1);
    expect(mocks.batch.mock.calls[0][0]).toHaveLength(2);
    expect(String(mocks.batch.mock.calls[0][0][0].sql)).toMatch(/FROM\s+open_orders/i);
    expect(String(mocks.batch.mock.calls[0][0][1].sql)).toMatch(/FROM\s+executed_orders/i);
    expect(mocks.batch.mock.calls[0][1]).toBe("read");
  });
});
