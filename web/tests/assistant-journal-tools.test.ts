import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Assistant journal tools (`get_realized_pnl`, `query_journal`).
 *
 * These are the tools the 2026-07-24 weekly-P&L incident was missing: the
 * registry had no journal/trade-history access at all, so the loop thrashed
 * knowledge searches until MAX_ROUNDS. Both tools hit Turso directly through
 * the journalRangeDb layer (mocked here); results are stringified verbatim
 * into the model context, so compactness is a hard contract.
 */

const mocks = vi.hoisted(() => ({
  fetchJournalRowsInRange: vi.fn(),
  fetchPriorRowsForTickers: vi.fn(),
}));

vi.mock("@/lib/journal/journalRangeDb", () => ({
  fetchJournalRowsInRange: mocks.fetchJournalRowsInRange,
  fetchPriorRowsForTickers: mocks.fetchPriorRowsForTickers,
  PRIOR_OPEN_LOOKBACK_DAYS: 180,
  WINDOW_ROW_LIMIT: 1_000,
}));

import { ASSISTANT_TOOLS, isDestructiveTool, toolSchemas } from "@/lib/assistant/tools";

const WINDOW = { from: "2026-07-19", to: "2026-07-24" };

function tool(name: string) {
  const found = ASSISTANT_TOOLS.find((t) => t.name === name);
  if (!found) throw new Error(`tool ${name} not registered`);
  return found;
}

function journalRow(
  tradeId: string,
  filledAt: string,
  payload: Record<string, unknown>,
) {
  return { trade_id: tradeId, filled_at: filledAt, payload: { ...payload, filled_at: filledAt } };
}

const SNDK_CLOSE = journalRow("sndk-c", "2026-07-21T15:02:00Z", {
  ticker: "SNDK", action: "BUY_TO_CLOSE", contracts: 4, fill_price: 62.8,
  commission: 6.84, strike: 1500, right: "P", expiry: "20260821", ib_exec_id: "sndkc1",
  notes: "should never surface in tool output",
});
const SNDK_PRIOR_OPEN = journalRow("sndk-o", "2026-07-16T14:31:00Z", {
  ticker: "SNDK", action: "SELL_TO_OPEN", contracts: 5, fill_price: 139.55,
  commission: 5.0, strike: 1500, right: "P", expiry: "20260821", ib_exec_id: "sndko1",
});
const AAPL_OPEN = journalRow("aapl-o", "2026-07-20T14:00:00Z", {
  ticker: "AAPL", action: "BUY_OPTION", contracts: 2, fill_price: 3.1,
  commission: 1.3, strike: 250, right: "C", expiry: "20261218", ib_exec_id: "aaplo1",
  notes: "open only, must not trigger a prior fetch",
});

beforeEach(() => {
  mocks.fetchJournalRowsInRange.mockReset();
  mocks.fetchPriorRowsForTickers.mockReset();
  mocks.fetchJournalRowsInRange.mockResolvedValue([]);
  mocks.fetchPriorRowsForTickers.mockResolvedValue([]);
});

describe("registry", () => {
  it("registers get_realized_pnl and query_journal as non-destructive READ tools", () => {
    const names = ASSISTANT_TOOLS.map((t) => t.name);
    expect(names).toEqual(expect.arrayContaining(["get_realized_pnl", "query_journal"]));

    for (const name of ["get_realized_pnl", "query_journal"]) {
      const t = tool(name);
      expect(t.destructive).toBe(false);
      expect(isDestructiveTool(name)).toBe(false);
      expect(typeof t.run).toBe("function");
      expect(t.input_schema).toMatchObject({ type: "object", required: ["from", "to"] });
    }

    const schemaNames = toolSchemas().map((s) => s.name);
    expect(schemaNames).toEqual(expect.arrayContaining(["get_realized_pnl", "query_journal"]));
  });
});

describe("get_realized_pnl", () => {
  it("fetches prior rows for exactly the tickers with in-window closes, beforeEt = from", async () => {
    mocks.fetchJournalRowsInRange.mockResolvedValue([SNDK_CLOSE, AAPL_OPEN]);
    mocks.fetchPriorRowsForTickers.mockResolvedValue([SNDK_PRIOR_OPEN]);

    const result = (await tool("get_realized_pnl").run!(WINDOW)) as Record<string, unknown>;

    expect(mocks.fetchJournalRowsInRange).toHaveBeenCalledWith(WINDOW.from, WINDOW.to);
    expect(mocks.fetchPriorRowsForTickers).toHaveBeenCalledTimes(1);
    expect(mocks.fetchPriorRowsForTickers).toHaveBeenCalledWith(["SNDK"], WINDOW.from);

    expect(typeof result.total_realized_pnl).toBe("number");
    expect(Array.isArray(result.round_trips)).toBe(true);
  });

  it("skips the prior fetch when the window has no closes", async () => {
    mocks.fetchJournalRowsInRange.mockResolvedValue([AAPL_OPEN]);
    await tool("get_realized_pnl").run!(WINDOW);
    expect(mocks.fetchPriorRowsForTickers).not.toHaveBeenCalled();
  });

  it("lot-matches the cross-window open into an in-window realized round trip", async () => {
    mocks.fetchJournalRowsInRange.mockResolvedValue([SNDK_CLOSE]);
    mocks.fetchPriorRowsForTickers.mockResolvedValue([SNDK_PRIOR_OPEN]);

    const result = (await tool("get_realized_pnl").run!(WINDOW)) as {
      total_realized_pnl: number;
      round_trips: Array<{ open_outside_window?: true }>;
    };
    const openNetCreditPerContract = (5 * 139.55 * 100 - 5.0) / 5;
    const expected = 4 * openNetCreditPerContract - (4 * 62.8 * 100 + 6.84);
    expect(result.total_realized_pnl).toBeCloseTo(expected, 2);
    expect(result.round_trips[0].open_outside_window).toBe(true);
  });

  it("rejects malformed dates, inverted windows, and oversized spans", async () => {
    const run = tool("get_realized_pnl").run!;
    await expect(run({ from: "07/19/2026", to: "2026-07-24" })).rejects.toThrow(/YYYY-MM-DD/);
    await expect(run({ from: "2026-07-24", to: "2026-07-19" })).rejects.toThrow(/before/);
    await expect(run({ from: "2020-01-01", to: "2026-07-24" })).rejects.toThrow(/366/);
    expect(mocks.fetchJournalRowsInRange).not.toHaveBeenCalled();
  });
});

describe("query_journal", () => {
  function manyRows(count: number) {
    return Array.from({ length: count }, (_, i) =>
      journalRow(`t-${i}`, `2026-07-${20 + (i % 3)}T1${i % 10}:0${i % 6}:00Z`, {
        ticker: i % 2 === 0 ? "SNDK" : "EWY",
        action: "BUY_OPTION",
        contracts: 1 + (i % 5),
        fill_price: 1.5 + i * 0.01,
        commission: 0.66,
        strike: 100 + i,
        right: "C",
        expiry: "20261218",
        ib_exec_id: `exec-${i}`,
        notes: "Rehydrated from IB Flex Query on 2026-07-21 — verbose provenance sentence.",
        decision: "IB_AUTO_IMPORT",
      }),
    );
  }

  it("defaults to 40 rows and hard-caps limit at 50, newest first", async () => {
    mocks.fetchJournalRowsInRange.mockResolvedValue(manyRows(60));
    const run = tool("query_journal").run!;

    const byDefault = (await run(WINDOW)) as { count: number; truncated: boolean; rows: Array<{ date: string }> };
    expect(byDefault.count).toBe(40);
    expect(byDefault.rows).toHaveLength(40);
    expect(byDefault.truncated).toBe(true);

    const capped = (await run({ ...WINDOW, limit: 100 })) as { count: number; rows: unknown[] };
    expect(capped.count).toBe(50);

    const dates = byDefault.rows.map((r) => r.date);
    const sortedDesc = [...dates].sort().reverse();
    expect(dates).toEqual(sortedDesc);
  });

  it("returns compact rows: family marker present, notes/decision never", async () => {
    mocks.fetchJournalRowsInRange.mockResolvedValue([SNDK_CLOSE, AAPL_OPEN]);
    const result = (await tool("query_journal").run!(WINDOW)) as {
      rows: Array<Record<string, unknown>>;
    };
    expect(result.rows.length).toBe(2);
    for (const r of result.rows) {
      expect(["flex_agg", "fill"]).toContain(r.family);
      expect(r).not.toHaveProperty("notes");
      expect(r).not.toHaveProperty("decision");
    }
  });

  it("marks fill twins of a Flex composite dup:true and returns both families", async () => {
    const flexAgg = journalRow("sndk-x", "2026-07-21T15:02:00Z", {
      ticker: "SNDK", action: "BUY_TO_CLOSE", contracts: 4, fill_price: 62.8,
      commission: 6.84, strike: 1500, right: "P", expiry: "20260821",
      ib_exec_id: "sndkc1+sndkc2", realized_pnl: 30689.16, cost_basis: 25126.84, proceeds: 55816,
    });
    const fillTwin = journalRow("sndk-f", "2026-07-21T15:02:00Z", {
      ticker: "SNDK", action: "BUY_TO_CLOSE", contracts: 1, fill_price: 62.8,
      commission: 1.71, strike: 1500, right: "P", expiry: "20260821", ib_exec_id: "sndkc1",
    });
    mocks.fetchJournalRowsInRange.mockResolvedValue([flexAgg, fillTwin]);

    const result = (await tool("query_journal").run!(WINDOW)) as {
      rows: Array<Record<string, unknown>>;
    };
    expect(result.rows).toHaveLength(2);
    const agg = result.rows.find((r) => r.family === "flex_agg");
    const fill = result.rows.find((r) => r.family === "fill");
    expect(agg?.dup).toBeUndefined();
    expect(fill?.dup).toBe(true);
    expect(agg?.realized_pnl).toBeCloseTo(30689.16, 2);
  });

  it("filters by ticker and window days", async () => {
    mocks.fetchJournalRowsInRange.mockResolvedValue([
      SNDK_CLOSE,
      AAPL_OPEN,
      journalRow("early", "2026-07-18T14:00:00Z", {
        ticker: "SNDK", action: "SELL_TO_OPEN", contracts: 1, fill_price: 1, ib_exec_id: "early1",
      }),
    ]);
    const result = (await tool("query_journal").run!({ ...WINDOW, ticker: "sndk" })) as {
      ticker: string;
      rows: Array<{ ticker: string; date: string }>;
    };
    expect(result.ticker).toBe("SNDK");
    expect(result.rows).toHaveLength(1);
    expect(result.rows[0].ticker).toBe("SNDK");
    expect(result.rows[0].date).toBe("2026-07-21");
  });

  it("keeps a 30-row payload under the compactness budget", async () => {
    mocks.fetchJournalRowsInRange.mockResolvedValue(manyRows(30));
    const result = await tool("query_journal").run!(WINDOW);
    expect(JSON.stringify(result).length).toBeLessThan(8000);
  });

  it("rejects malformed dates", async () => {
    await expect(tool("query_journal").run!({ from: "next week", to: "2026-07-24" }))
      .rejects.toThrow(/YYYY-MM-DD/);
  });
});
