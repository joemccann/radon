/**
 * Tests for journalRowsToBlotter — projects Turso `journal` rows into
 * the BlotterPayload shape consumed by HistoricalTradesSection.
 */

import { describe, it, expect } from "vitest";
import {
  journalRowsToBlotter,
  type JournalRow,
} from "../lib/blotter/fromJournal";

function row(payload: Record<string, unknown>, filled_at?: string): JournalRow {
  return { payload: payload as JournalRow["payload"], filled_at: filled_at ?? null };
}

describe("journalRowsToBlotter", () => {
  it("projects a closed call with realized P&L into closed_trades", () => {
    const rows: JournalRow[] = [
      row(
        {
          id: 1,
          date: "2026-04-29",
          ticker: "AAOI",
          structure: "Closed Call $155 2026-05-01",
          decision: "IB_AUTO_IMPORT",
          action: "SELL_OPTION",
          fill_price: 7.5676,
          total_cost: 37840.92,
          contracts: 50,
          commission: 2.92,
          ib_exec_id: "abc123",
          realized_pnl: 165.0,
          right: "C",
          strike: 155,
          expiry: "20260501",
        },
        "2026-04-29",
      ),
    ];

    const out = journalRowsToBlotter(rows);

    expect(out.summary.closed_trades).toBe(1);
    expect(out.summary.open_trades).toBe(0);
    expect(out.summary.realized_pnl).toBeCloseTo(165.0, 4);
    expect(out.closed_trades).toHaveLength(1);
    const trade = out.closed_trades[0];
    expect(trade.symbol).toBe("AAOI");
    expect(trade.is_closed).toBe(true);
    expect(trade.realized_pnl).toBeCloseTo(165.0, 4);
    expect(trade.sec_type).toBe("OPT");
    expect(trade.executions).toHaveLength(1);
    expect(trade.executions[0].side).toBe("SLD");
    expect(trade.executions[0].quantity).toBe(50);
    // cost_basis ≈ total_cost - realized_pnl  (so % math stays sensible)
    expect(trade.cost_basis).toBeCloseTo(37675.92, 1);
    expect(trade.proceeds).toBeCloseTo(37840.92, 1);
  });

  it("projects a closed BAG (spread) row into closed_trades with sec_type BAG", () => {
    const rows: JournalRow[] = [
      row(
        {
          id: 2,
          date: "2026-04-22",
          ticker: "NVDA",
          structure: "Closed Spread (BAG)",
          action: "SELL_TO_OPEN",
          fill_price: 2.005,
          total_cost: 10025,
          contracts: 50,
          commission: 12.5,
          ib_exec_id: "bag-xyz",
          realized_pnl: 4321.7,
        },
        "2026-04-22",
      ),
    ];

    const out = journalRowsToBlotter(rows);
    expect(out.closed_trades).toHaveLength(1);
    const trade = out.closed_trades[0];
    expect(trade.sec_type).toBe("BAG");
    expect(trade.is_closed).toBe(true);
    expect(trade.contract_desc).toContain("NVDA");
    expect(trade.contract_desc).toContain("Spread");
    expect(trade.realized_pnl).toBeCloseTo(4321.7, 4);
  });

  it("projects an open BUY_OPTION row into open_trades", () => {
    const rows: JournalRow[] = [
      row(
        {
          id: 3,
          date: "2026-05-01",
          ticker: "TSLA",
          structure: "Long Call $250 2026-06-19",
          action: "BUY_OPTION",
          fill_price: 18.0,
          total_cost: 3601.4,
          contracts: 2,
          commission: 1.4,
          ib_exec_id: "tsla-open",
          right: "C",
          strike: 250,
          expiry: "20260619",
        },
        "2026-05-01",
      ),
    ];

    const out = journalRowsToBlotter(rows);
    expect(out.summary.closed_trades).toBe(0);
    expect(out.summary.open_trades).toBe(1);
    const trade = out.open_trades[0];
    expect(trade.is_closed).toBe(false);
    expect(trade.symbol).toBe("TSLA");
    expect(trade.net_quantity).toBe(2);
    expect(trade.executions[0].side).toBe("BOT");
    expect(trade.executions[0].quantity).toBe(2);
    // BOT exec → cash flows out
    expect(trade.executions[0].net_cash_flow).toBeLessThan(0);
  });

  it("preserves multi-fill execution detail via composite ib_exec_id", () => {
    // journal_rehydrate joins multi-fill executions with '+'. We project
    // one row with one execution but the composite id round-trips so the
    // /orders panel can still link back to IB if needed.
    const compositeId = "exec-1+exec-2+exec-3";
    const rows: JournalRow[] = [
      row(
        {
          id: 10,
          date: "2026-04-30",
          ticker: "AMD",
          structure: "Closed Put $145 2026-05-01",
          action: "SELL_OPTION",
          fill_price: 5.5,
          total_cost: 27500,
          contracts: 50,
          commission: 2.5,
          ib_exec_id: compositeId,
          realized_pnl: 1200,
        },
        "2026-04-30",
      ),
    ];

    const out = journalRowsToBlotter(rows);
    expect(out.closed_trades[0].executions[0].exec_id).toBe(compositeId);
    expect(out.closed_trades[0].executions[0].quantity).toBe(50);
    expect(out.closed_trades[0].executions[0].price).toBe(5.5);
  });

  it("returns an empty payload (no error) when journal is empty", () => {
    const out = journalRowsToBlotter([]);
    expect(out.as_of).toBe("");
    expect(out.summary).toEqual({
      closed_trades: 0,
      open_trades: 0,
      total_commissions: 0,
      realized_pnl: 0,
    });
    expect(out.closed_trades).toEqual([]);
    expect(out.open_trades).toEqual([]);
  });

  it("derives as_of from the most recent filled_at", () => {
    const rows: JournalRow[] = [
      row(
        { ticker: "A", action: "BUY_OPTION", contracts: 1, fill_price: 1, total_cost: 100 },
        "2026-04-01",
      ),
      row(
        { ticker: "B", action: "SELL_OPTION", contracts: 1, fill_price: 2, total_cost: 200, realized_pnl: 50 },
        "2026-05-01",
      ),
      row(
        { ticker: "C", action: "BUY", shares: 100, fill_price: 50, total_cost: 5000 },
        "2026-04-15",
      ),
    ];

    const out = journalRowsToBlotter(rows);
    expect(out.as_of).toBe("2026-05-01");
  });

  it("flags as_of older than threshold (consumer pill logic stays correct)", () => {
    // The pill is rendered by HistoricalTradesSection from data.as_of.
    // We just need to confirm the deriver hands a usable timestamp through.
    const tenDaysAgo = new Date(Date.now() - 10 * 24 * 60 * 60 * 1000)
      .toISOString();
    const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
    const stale = journalRowsToBlotter([
      row({ ticker: "X", action: "SELL_OPTION", contracts: 1, fill_price: 1, total_cost: 100, realized_pnl: 10 }, tenDaysAgo),
    ]);
    const fresh = journalRowsToBlotter([
      row({ ticker: "X", action: "SELL_OPTION", contracts: 1, fill_price: 1, total_cost: 100, realized_pnl: 10 }, yesterday),
    ]);
    const ageDays = (asOf: string) =>
      Math.floor((Date.now() - Date.parse(asOf)) / (24 * 60 * 60 * 1000));
    expect(ageDays(stale.as_of)).toBeGreaterThan(1); // pill threshold = 1
    expect(ageDays(fresh.as_of)).toBeLessThanOrEqual(1);
  });

  it("aggregates total_commissions and realized_pnl across rows", () => {
    const rows: JournalRow[] = [
      row(
        { ticker: "A", action: "SELL_OPTION", contracts: 1, fill_price: 5, total_cost: 500, commission: 1.25, realized_pnl: 100 },
        "2026-04-30",
      ),
      row(
        { ticker: "B", action: "SELL_OPTION", contracts: 2, fill_price: 4, total_cost: 800, commission: 2.5, realized_pnl: -50 },
        "2026-04-29",
      ),
      row(
        { ticker: "C", action: "BUY_OPTION", contracts: 1, fill_price: 2, total_cost: 200, commission: 1 },
        "2026-04-28",
      ),
    ];

    const out = journalRowsToBlotter(rows);
    expect(out.summary.closed_trades).toBe(2);
    expect(out.summary.open_trades).toBe(1);
    expect(out.summary.total_commissions).toBeCloseTo(4.75, 4);
    expect(out.summary.realized_pnl).toBeCloseTo(50, 4);
  });

  /* ─── Live-session scenario coverage ───────────────────────────── */

  it("Scenario A — picks up a fresh mid-session fill in the next /api/blotter response", () => {
    // fill_monitor / journal_sync inserts a row with filled_at = now()
    // immediately. Next GET must surface it without REFRESH being clicked.
    const now = new Date().toISOString();
    const rows: JournalRow[] = [
      row(
        {
          id: 999,
          ticker: "SPY",
          structure: "Long Call $470 2026-05-30",
          action: "BUY_OPTION",
          fill_price: 4.10,
          total_cost: 1640,
          contracts: 4,
          commission: 1.6,
          ib_exec_id: "live-fill-001",
          right: "C",
          strike: 470,
          expiry: "20260530",
        },
        now,
      ),
    ];
    const out = journalRowsToBlotter(rows);
    expect(out.as_of).toBe(now);
    // BUY_OPTION with no realized_pnl → open_trades.
    expect(out.open_trades).toHaveLength(1);
    expect(out.closed_trades).toHaveLength(0);
    const trade = out.open_trades[0];
    expect(trade.symbol).toBe("SPY");
    expect(trade.is_closed).toBe(false);
    expect(trade.executions[0].time).toBe(now);
    // Age between now and as_of is ~0 — STALE pill should never fire.
    const ageMs = Date.now() - Date.parse(out.as_of);
    expect(ageMs).toBeLessThan(5_000);
  });

  it("Scenario B — open position with no realized_pnl renders without crashing the consumer", () => {
    // Open trade: status=OPEN, realized_pnl=null, entry_cost > 0.
    // The consumer (HistoricalTradesSection) reads:
    //   t.executions[…].time, t.symbol, t.contract_desc, t.sec_type,
    //   t.is_closed, t.total_quantity ?? t.net_quantity, t.total_commission,
    //   t.realized_pnl, t.cost_basis, t.proceeds.
    // All of these MUST be non-undefined (or null, where typed) for the
    // OPEN badge path; otherwise React renders 'undefined' literals.
    const rows: JournalRow[] = [
      row(
        {
          id: 1001,
          ticker: "AMD",
          structure: "Long Put $145 2026-05-15",
          action: "BUY_OPTION",
          fill_price: 3.25,
          total_cost: 1625,
          contracts: 5,
          commission: 2.5,
          ib_exec_id: "amd-open-1",
          right: "P",
          strike: 145,
          expiry: "20260515",
        },
        "2026-05-01T15:30:00Z",
      ),
    ];
    const out = journalRowsToBlotter(rows);
    const trade = out.open_trades[0];

    expect(trade.is_closed).toBe(false);
    expect(trade.realized_pnl).toBeNull();
    expect(trade.cost_basis).toBe(1625); // = entry_cost
    expect(trade.proceeds).toBe(0);      // no exit yet
    expect(trade.net_quantity).toBe(5);  // BUY_OPTION → +qty
    expect(trade.total_quantity).toBe(5);
    expect(trade.total_commission).toBe(2.5);
    expect(trade.executions).toHaveLength(1);
    expect(trade.executions[0].side).toBe("BOT");
    expect(trade.executions[0].time).toBe("2026-05-01T15:30:00Z");
    // Every field the consumer reads must be defined (no `undefined`).
    const keys: Array<keyof typeof trade> = [
      "symbol", "contract_desc", "sec_type", "is_closed", "net_quantity",
      "total_quantity", "total_commission", "realized_pnl", "cost_basis",
      "proceeds", "executions",
    ];
    for (const k of keys) expect(trade[k]).not.toBeUndefined();
  });

  it("Scenario C — a 30+30+40 multi-fill order projects to ONE blotter row, not three", () => {
    // journal_rehydrate.py:_composite_exec_id joins exec ids with '+'.
    // The deriver must NOT split that back into multiple blotter rows.
    const compositeId = "0001.exec1+0001.exec2+0001.exec3";
    const rows: JournalRow[] = [
      row(
        {
          id: 5500,
          ticker: "QQQ",
          structure: "Long Call $480 2026-06-19",
          action: "BUY_OPTION",
          fill_price: 6.25,
          total_cost: 62500, // 100 contracts × 6.25 × 100
          contracts: 100,    // already aggregated upstream
          commission: 4.5,
          ib_exec_id: compositeId,
          right: "C",
          strike: 480,
          expiry: "20260619",
        },
        "2026-05-02T14:45:00Z",
      ),
    ];
    const out = journalRowsToBlotter(rows);
    expect(out.open_trades).toHaveLength(1);
    expect(out.closed_trades).toHaveLength(0);
    const trade = out.open_trades[0];
    // ONE trade, with ONE composite-execution row (not three).
    expect(trade.executions).toHaveLength(1);
    expect(trade.total_quantity).toBe(100);
    expect(trade.net_quantity).toBe(100);
    // Composite id round-trips so the panel can still link back to IB.
    expect(trade.executions[0].exec_id).toBe(compositeId);
    expect(trade.executions[0].quantity).toBe(100);
    // Notional = 100 × 6.25 × 100 = 62500.
    expect(trade.executions[0].notional_value).toBeCloseTo(62500, 2);
  });

  it("Scenario D — POST fallback: derived payload survives Flex Query failure", async () => {
    // Mock the route handler's fallback path. We can't import the route
    // (Next.js wiring), but we replicate the contract: when the live
    // sync throws, the route MUST return the journal-derived payload
    // with status 200 — never 502 — so /orders keeps showing trades.
    const journalRows: JournalRow[] = [
      row(
        {
          id: 7777,
          ticker: "TLT",
          structure: "Closed Put $90 2026-05-10",
          action: "SELL_OPTION",
          fill_price: 1.25,
          total_cost: 1250,
          contracts: 10,
          commission: 1.0,
          ib_exec_id: "tlt-closed-1",
          realized_pnl: 305,
        },
        "2026-05-02T18:00:00Z",
      ),
    ];

    async function simulatePost(): Promise<{ status: number; body: unknown }> {
      try {
        // simulate radonFetch("/blotter", { method: "POST" }) failing
        throw new Error("Statement could not be generated. Please try again");
      } catch {
        const fallback = journalRowsToBlotter(journalRows);
        if (fallback.summary.closed_trades + fallback.summary.open_trades > 0) {
          return { status: 200, body: fallback };
        }
        return { status: 502, body: { error: "blotter sync failed" } };
      }
    }

    const result = await simulatePost();
    expect(result.status).toBe(200);
    const body = result.body as ReturnType<typeof journalRowsToBlotter>;
    expect(body.summary.closed_trades).toBe(1);
    expect(body.closed_trades[0].symbol).toBe("TLT");
    expect(body.closed_trades[0].realized_pnl).toBeCloseTo(305, 4);
    // Sanity: as_of is the journal MAX(filled_at), not 1970/null.
    expect(body.as_of).toBe("2026-05-02T18:00:00Z");
  });
});
