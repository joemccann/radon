import { beforeEach, describe, expect, it, vi } from "vitest";

const execute = vi.fn();
vi.mock("@/lib/db", () => ({ getDb: () => ({ execute }) }));

import { importLatestReconciliationToJournal } from "@/lib/journalDb";

describe("journal reconciliation concurrency", () => {
  beforeEach(() => execute.mockReset());

  it("cannot overwrite a richer row inserted after the initial read", async () => {
    execute
      .mockResolvedValueOnce({ rows: [{ payload: JSON.stringify({ new_trades: [{
        symbol: "SPY", date: "2026-08-13", action: "BUY", net_quantity: 1,
        avg_price: 600, commission: 1, realized_pnl: 0, sec_type: "STK", ib_exec_id: "exec-1",
      }] }) }] })
      .mockResolvedValueOnce({ rows: [] })
      .mockResolvedValueOnce({ rowsAffected: 0, rows: [] });

    await importLatestReconciliationToJournal();

    const insert = execute.mock.calls[2][0];
    expect(insert.sql).toMatch(/ON CONFLICT\(trade_id\) DO NOTHING/);
    expect(insert.sql).not.toMatch(/DO UPDATE/);
  });
});
