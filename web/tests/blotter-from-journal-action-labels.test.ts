/**
 * fromJournal reads the shared journal action labels
 * (scripts/utils/journal_action.py, audit §1 #11):
 *   - a stock short sale is SELL_TO_OPEN (open), its cover BUY_TO_CLOSE;
 *   - the legacy ib_reconcile stock label SHORT is an open short too;
 *   - a fill that crosses zero carries `flip_open_qty`: the closing part
 *     realizes, the residual stays open on the other side.
 */
import { describe, expect, it } from "vitest";
import { journalRowsToBlotter, type JournalRow } from "../lib/blotter/fromJournal";

function row(payload: Record<string, unknown>, filled_at = "2026-11-02T15:00:00Z"): JournalRow {
  return { payload: payload as JournalRow["payload"], filled_at };
}

describe("fromJournal shared action labels", () => {
  it("renders a rehydrated stock short sale (explicit P&L fields) as an open short", () => {
    const out = journalRowsToBlotter([
      row({
        ticker: "AAOI", action: "SELL_TO_OPEN", shares: 100, fill_price: 10,
        total_cost: 999, cost_basis: 0, proceeds: 999, realized_pnl: 0,
        ib_exec_id: "9001", structure: "Short Stock (STK)",
      }),
    ]);
    expect(out.closed_trades).toHaveLength(0);
    expect(out.open_trades).toHaveLength(1);
    expect(out.open_trades[0].net_quantity).toBe(-100);
  });

  it("reads the legacy SHORT stock label as an open short, not a flat row", () => {
    const out = journalRowsToBlotter([
      row({
        ticker: "AAOI", action: "SHORT", shares: 100, fill_price: 10,
        total_cost: 999, realized_pnl: 0, ib_exec_id: "R-1",
      }),
    ]);
    expect(out.open_trades).toHaveLength(1);
    expect(out.open_trades[0].net_quantity).toBe(-100);
    expect(out.open_trades[0].executions[0].side).toBe("SLD");
  });

  it("splits a flip row: the close realizes, the residual stays open short", () => {
    const out = journalRowsToBlotter([
      row({
        ticker: "AAOI", action: "SELL_OPTION", contracts: 8, flip_open_qty: 3,
        strike: 40, right: "C", expiry: "20261218", fill_price: 2,
        total_cost: 1600, commission: 8, cost_basis: 1000, proceeds: 992,
        realized_pnl: 25, realized_quantity: 5, ib_exec_id: "9002",
        structure: "Closed Call $40 2026-12-18",
      }),
    ]);
    expect(out.closed_trades).toHaveLength(1);
    expect(out.closed_trades[0].executions[0].quantity).toBe(5);
    expect(out.closed_trades[0].realized_pnl).toBe(25);
    expect(out.open_trades).toHaveLength(1);
    expect(out.open_trades[0].net_quantity).toBe(-3);
    expect(out.open_trades[0].realized_pnl).toBeNull();
    expect(out.summary.realized_pnl).toBe(25);
    expect(out.summary.total_commissions).toBeCloseTo(8);
  });

  it("splits a real-time short-to-long flip: residual opens long", () => {
    const out = journalRowsToBlotter([
      row({
        ticker: "AAOI", action: "SELL_TO_OPEN", contracts: 2, strike: 40, right: "C",
        expiry: "20261218", fill_price: 3, total_cost: 600, ib_exec_id: "0001.a.01",
      }, "2026-11-01T15:00:00Z"),
      row({
        ticker: "AAOI", action: "BUY_TO_CLOSE", contracts: 7, flip_open_qty: 5, strike: 40,
        right: "C", expiry: "20261218", fill_price: 1, total_cost: 700, ib_exec_id: "0001.b.01",
      }),
    ]);
    const residual = out.open_trades.filter((t) => t.net_quantity > 0);
    expect(residual).toHaveLength(1);
    expect(residual[0].net_quantity).toBe(5);
    const cover = out.closed_trades.find((t) => t.executions[0].exec_id === "0001.b.01");
    expect(cover?.executions[0].quantity).toBe(2);
    expect(cover?.executions[0].side).toBe("BOT");
  });
});
