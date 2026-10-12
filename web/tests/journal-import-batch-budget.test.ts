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

  it("updates a corrected fill inside a write batch and inserts a new fill as nothing", async () => {
    const existing = {
      id: 7,
      date: "2026-03-09",
      ticker: "URTY",
      action: "BUY",
      shares: 2000,
      fill_price: 55.997,
      ib_exec_id: "ORDER.01",
    };
    execute.mockImplementation(async (stmt: { sql?: string }) => {
      const sql = String(stmt.sql);
      if (sql.includes("reconciliation_log")) {
        return { rows: [{ payload: JSON.stringify({ new_trades: [
          {
            symbol: "URTY", date: "2026-03-09", action: "BUY", net_quantity: 2000,
            avg_price: 56.5, commission: 10, realized_pnl: 0, sec_type: "STK",
            ib_exec_id: "ORDER.02",
          },
          trade(3),
        ] }) }] };
      }
      if (sql.includes("FROM journal")) {
        return { rows: [{ payload: JSON.stringify(existing) }] };
      }
      return { rows: [] };
    });

    const result = await importLatestReconciliationToJournal();
    expect(result.imported).toBe(2);
    expect(batch).toHaveBeenCalledTimes(1);
    expect(batch.mock.calls[0][1]).toBe("write");
    const statements = batch.mock.calls[0][0] as Array<{ sql: string; args: unknown[] }>;
    const correction = statements.find((stmt) => stmt.args[0] === "ORDER.01");
    const fresh = statements.find((stmt) => stmt.args[0] === "exec-3");
    expect(correction?.sql).toMatch(/ON CONFLICT\(trade_id\) DO UPDATE SET/);
    expect(correction?.sql).not.toMatch(/DO NOTHING/);
    const payload = JSON.parse(String(correction?.args[1]));
    expect(payload.ib_exec_id).toBe("ORDER.01");
    expect(payload.ib_exec_id_corrected).toBe("ORDER.02");
    expect(payload.fill_price).toBe(56.5);
    expect(fresh?.sql).toMatch(/ON CONFLICT\(trade_id\) DO NOTHING/);
    expect(fresh?.sql).not.toMatch(/DO UPDATE/);
  });
});
