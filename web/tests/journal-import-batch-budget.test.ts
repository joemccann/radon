import { beforeEach, describe, expect, it, vi } from "vitest";

const execute = vi.fn();
const batch = vi.fn(async () => []);
vi.mock("@/lib/db", () => ({ getDb: () => ({ execute, batch }) }));

import { importLatestReconciliationToJournal } from "@/lib/journalDb";

function trade(n: number) {
  return {
    symbol: "SPY",
    date: "2026-10-09",
    action: "BUY",
    net_quantity: 1,
    avg_price: 600,
    commission: 1,
    realized_pnl: 0,
    sec_type: "STK",
    ib_exec_id: `exec-${n}`,
  };
}

function stubReads(count: number) {
  execute.mockImplementation(async (stmt: { sql?: string }) => {
    if (String(stmt.sql).includes("reconciliation_log")) {
      return { rows: [{ payload: JSON.stringify({ new_trades: Array.from({ length: count }, (_, i) => trade(i)) }) }] };
    }
    return { rows: [] };
  });
}

describe("journal import Turso round-trip budget", () => {
  beforeEach(() => {
    execute.mockReset();
    batch.mockClear();
    batch.mockImplementation(async () => []);
  });

  it("sends 40 new fills in one batch, not 40 executes", async () => {
    stubReads(40);
    const result = await importLatestReconciliationToJournal();
    expect(result.imported).toBe(40);
    const insertExecutes = execute.mock.calls.filter(([stmt]) => String(stmt?.sql).includes("INSERT INTO journal"));
    expect(insertExecutes).toHaveLength(0);
    expect(batch).toHaveBeenCalledTimes(1);
    expect(batch.mock.calls[0][0]).toHaveLength(40);
    expect(String(batch.mock.calls[0][0][0].sql)).toMatch(/ON CONFLICT\(trade_id\) DO NOTHING/);
  });

  it("caps each batch at 50 statements", async () => {
    stubReads(101);
    const result = await importLatestReconciliationToJournal();
    expect(result.imported).toBe(101);
    expect(batch).toHaveBeenCalledTimes(3);
    const sizes = batch.mock.calls.map((call) => call[0].length);
    expect(sizes).toEqual([50, 50, 1]);
    expect(Math.max(...sizes)).toBeLessThanOrEqual(50);
  });
});
