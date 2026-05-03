/**
 * Tests for journalRowsToBlotter — projects Turso `journal` rows into
 * the BlotterPayload shape consumed by HistoricalTradesSection.
 */

import { describe, it, expect } from "vitest";
import {
  journalRowsToBlotter,
  type BlotterPayload,
  type BlotterTradeShape,
  type JournalRow,
} from "../lib/blotter/fromJournal";

function legacyTrade(overrides: Partial<BlotterTradeShape>): BlotterTradeShape {
  return {
    symbol: "?",
    contract_desc: "",
    sec_type: "OPT",
    is_closed: true,
    net_quantity: 0,
    total_quantity: 0,
    total_commission: 0,
    realized_pnl: null,
    cost_basis: 0,
    proceeds: 0,
    total_cash_flow: 0,
    executions: [],
    ...overrides,
  };
}

function legacyPayload(trades: BlotterTradeShape[], asOf = "2026-03-26T00:00:00Z"): BlotterPayload {
  const closed = trades.filter((t) => t.is_closed);
  const open = trades.filter((t) => !t.is_closed);
  return {
    as_of: asOf,
    summary: {
      closed_trades: closed.length,
      open_trades: open.length,
      total_commissions: trades.reduce((a, t) => a + (t.total_commission || 0), 0),
      realized_pnl: closed.reduce((a, t) => a + (t.realized_pnl ?? 0), 0),
    },
    closed_trades: closed,
    open_trades: open,
  };
}

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

  /* ─── Union + preference fallback ──────────────────────────────────── */

  describe("legacy blotter union + preference fallback", () => {
    it("journal row lacks P&L → uses legacy P&L when exec_id matches", () => {
      // Pre-bbc776e journal row: aggregated buy+sell into one volume-
      // weighted record. cost_basis / proceeds / realized_pnl are absent.
      const rows: JournalRow[] = [
        row(
          {
            id: 1,
            ticker: "AAPL",
            structure: "Closed Call $190 2026-04-30",
            action: "CLOSED",
            fill_price: 5.0,
            total_cost: 25000,
            contracts: 50,
            commission: 5,
            ib_exec_id: "legacy-exec-1",
          },
          "2026-04-30",
        ),
      ];
      const legacy = legacyPayload([
        legacyTrade({
          symbol: "AAPL",
          contract_desc: "AAPL Closed Call $190",
          sec_type: "OPT",
          is_closed: true,
          total_quantity: 50,
          total_commission: 5,
          realized_pnl: 1234.56,
          cost_basis: 22000,
          proceeds: 23234.56,
          total_cash_flow: 1234.56,
          executions: [
            {
              exec_id: "legacy-exec-1",
              time: "2026-04-30T09:30:00",
              side: "BOT",
              quantity: 50,
              price: 4.4,
              commission: 2.5,
              notional_value: 22000,
              net_cash_flow: -22002.5,
            },
          ],
        }),
      ]);

      const out = journalRowsToBlotter(rows, legacy);
      expect(out.summary.closed_trades).toBe(1);
      const trade = out.closed_trades[0];
      expect(trade.symbol).toBe("AAPL"); // journal-fresh metadata
      expect(trade.realized_pnl).toBeCloseTo(1234.56, 4);
      expect(trade.cost_basis).toBeCloseTo(22000, 2);
      expect(trade.proceeds).toBeCloseTo(23234.56, 2);
      expect(trade.is_closed).toBe(true);
    });

    it("journal row has explicit P&L (post-bbc776e) → ignores legacy", () => {
      const rows: JournalRow[] = [
        row(
          {
            id: 2,
            ticker: "MSFT",
            structure: "Closed Put $400 2026-04-30",
            action: "SELL_OPTION",
            fill_price: 3.0,
            total_cost: 15000,
            contracts: 50,
            commission: 2.5,
            ib_exec_id: "msft-exec-1",
            // Explicit fields — journal_rehydrate ran w/ bbc776e in effect.
            realized_pnl: 999.99,
            cost_basis: 14000,
            proceeds: 14999.99,
          },
          "2026-04-30",
        ),
      ];
      const legacy = legacyPayload([
        legacyTrade({
          symbol: "MSFT",
          is_closed: true,
          total_commission: 2.5,
          // Different (stale) numbers — journal must win.
          realized_pnl: 111.11,
          cost_basis: 1,
          proceeds: 2,
          executions: [
            {
              exec_id: "msft-exec-1",
              time: "2026-04-30T09:30:00",
              side: "SLD",
              quantity: 50,
              price: 3.0,
              commission: 2.5,
              notional_value: 15000,
              net_cash_flow: 14997.5,
            },
          ],
        }),
      ]);

      const out = journalRowsToBlotter(rows, legacy);
      const trade = out.closed_trades[0];
      expect(trade.realized_pnl).toBeCloseTo(999.99, 4);
      expect(trade.cost_basis).toBeCloseTo(14000, 2);
      expect(trade.proceeds).toBeCloseTo(14999.99, 2);
    });

    it("trade only in legacy → spliced into union output", () => {
      const rows: JournalRow[] = [
        row(
          {
            id: 1,
            ticker: "NVDA",
            structure: "Closed Call",
            action: "SELL_OPTION",
            fill_price: 1,
            total_cost: 100,
            contracts: 1,
            commission: 0.5,
            ib_exec_id: "in-journal",
            realized_pnl: 50,
            cost_basis: 50,
            proceeds: 100,
          },
          "2026-04-15",
        ),
      ];
      const legacy = legacyPayload([
        legacyTrade({
          symbol: "GOOG",
          contract_desc: "GOOG Closed Spread",
          is_closed: true,
          total_commission: 1.0,
          realized_pnl: 250,
          cost_basis: 1000,
          proceeds: 1250,
          executions: [
            {
              exec_id: "legacy-only-1",
              time: "2026-02-01",
              side: "SLD",
              quantity: 10,
              price: 125,
              commission: 1.0,
              notional_value: 1250,
              net_cash_flow: 1249,
            },
          ],
        }),
      ]);

      const out = journalRowsToBlotter(rows, legacy);
      // Both should be present.
      expect(out.summary.closed_trades).toBe(2);
      const symbols = out.closed_trades.map((t) => t.symbol).sort();
      expect(symbols).toEqual(["GOOG", "NVDA"]);
      const goog = out.closed_trades.find((t) => t.symbol === "GOOG")!;
      expect(goog.realized_pnl).toBeCloseTo(250, 4);
    });

    it("trade only in journal (new fill) → passes through unchanged", () => {
      const rows: JournalRow[] = [
        row(
          {
            id: 1,
            ticker: "SPY",
            structure: "Long Call $470 2026-05-30",
            action: "BUY_OPTION",
            fill_price: 4.10,
            total_cost: 1640,
            contracts: 4,
            commission: 1.6,
            ib_exec_id: "post-326-fill",
            right: "C",
            strike: 470,
            expiry: "20260530",
          },
          "2026-04-15T15:30:00Z",
        ),
      ];
      const legacy = legacyPayload([
        legacyTrade({
          symbol: "OTHER",
          is_closed: true,
          realized_pnl: 99,
          cost_basis: 1,
          proceeds: 100,
          executions: [
            {
              exec_id: "unrelated",
              time: "2026-01-01",
              side: "BOT",
              quantity: 1,
              price: 1,
              commission: 0,
              notional_value: 1,
              net_cash_flow: -1,
            },
          ],
        }),
      ]);

      const out = journalRowsToBlotter(rows, legacy);
      // Journal row → open, legacy unrelated → spliced into closed.
      expect(out.summary.open_trades).toBe(1);
      expect(out.summary.closed_trades).toBe(1);
      const spy = out.open_trades[0];
      expect(spy.symbol).toBe("SPY");
      expect(spy.is_closed).toBe(false);
      // Journal row had no explicit P&L AND no legacy match — falls back
      // to the row-level heuristic.
      expect(spy.cost_basis).toBe(1640);
      expect(spy.proceeds).toBe(0);
    });

    it("composite exec_id 'a+b' matches legacy exec_id 'a'", () => {
      // journal_rehydrate.py joins multi-fill exec ids with '+'. Legacy
      // blotter.json stores each fill separately; the deriver must match
      // on any constituent.
      const rows: JournalRow[] = [
        row(
          {
            id: 1,
            ticker: "AMD",
            structure: "Closed Call $200",
            action: "CLOSED",
            fill_price: 2.0,
            total_cost: 4000,
            contracts: 20,
            commission: 1.5,
            ib_exec_id: "fill-a+fill-b+fill-c",
          },
          "2026-04-20",
        ),
      ];
      const legacy = legacyPayload([
        legacyTrade({
          symbol: "AMD",
          is_closed: true,
          total_commission: 1.5,
          realized_pnl: 567.89,
          cost_basis: 3500,
          proceeds: 4067.89,
          executions: [
            // The legacy trade keys on the second leg only — we must still
            // resolve via the composite-split fallback.
            {
              exec_id: "fill-b",
              time: "2026-04-20T10:00:00",
              side: "SLD",
              quantity: 20,
              price: 2.0,
              commission: 1.5,
              notional_value: 4000,
              net_cash_flow: 3998.5,
            },
          ],
        }),
      ]);

      const out = journalRowsToBlotter(rows, legacy);
      expect(out.summary.closed_trades).toBe(1);
      const trade = out.closed_trades[0];
      expect(trade.symbol).toBe("AMD");
      expect(trade.realized_pnl).toBeCloseTo(567.89, 4);
      expect(trade.cost_basis).toBeCloseTo(3500, 2);
      expect(trade.proceeds).toBeCloseTo(4067.89, 2);
    });

    it("as_of = MAX(journal max filled_at, legacy as_of)", () => {
      const rows: JournalRow[] = [
        row(
          { ticker: "X", action: "BUY_OPTION", contracts: 1, fill_price: 1, total_cost: 100 },
          "2026-05-01T00:00:00Z",
        ),
      ];
      const olderLegacy = legacyPayload([], "2026-03-26T00:00:00Z");
      expect(journalRowsToBlotter(rows, olderLegacy).as_of).toBe("2026-05-01T00:00:00Z");

      const newerLegacy = legacyPayload([], "2027-01-01T00:00:00Z");
      expect(journalRowsToBlotter(rows, newerLegacy).as_of).toBe("2027-01-01T00:00:00Z");
    });
  });
});
