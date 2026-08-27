/**
 * @vitest-environment jsdom
 *
 * One market value per position.
 *
 * Twice now, a position's Today P&L has contradicted its own total P&L on a
 * surface — a stock-only inline copy of Today P&L (2026-08-11) and a
 * card-local real-time market-value walk (2026-08-26, META 40x short $580 put:
 * +$1,097 total against -$103 today). In both cases the shared helper was
 * correct and the surface had quietly grown a second opinion.
 *
 * `resolveRealtimeMarketValue` in `lib/positionUtils.ts` is the walk; every
 * surface calls it and falls back with `?? resolveMarketValue(pos)`.
 *
 * The pin is BEHAVIOURAL first: each surface is rendered against a quote whose
 * `last` sits outside its own bid/ask, and the market value it publishes must
 * be the shared resolver's to the dollar. A source-text matcher alone was not
 * enough — the version of this file written to stop the 2026-08-26 regression
 * was green against that exact regression, because its regex required a
 * `sign`-named identifier and a `mv`-shaped one on the same line and the real
 * defect spelled both differently (T-195). The static checks below stay as a
 * cheap tripwire and now self-verify against the historical defect line.
 *
 * A walk over legs for something OTHER than market value (Greeks, per-leg
 * rows, implied value, close-based daily figures) is fine — this pins the
 * accumulation of a price × contracts × multiplier TOTAL, which is the only
 * thing that can disagree with itself.
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import PositionTable from "@/components/PositionTable";
import MobilePositionList from "@/components/mobile/MobilePositionList";
import PositionTab from "@/components/ticker-detail/PositionTab";
import {
  fmtUsd,
  resolveMarketValue,
  resolveRealtimeMarketValue,
} from "@/lib/positionUtils";
import { optionKey } from "@/lib/pricesProtocol";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioPosition } from "@/lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

afterEach(cleanup);

const WEB_ROOT = join(__dirname, "..");

/** Surfaces that render a position's market value, P&L, or Today P&L. */
const POSITION_SURFACES = [
  "components/PositionTable.tsx",
  "components/mobile/MobilePositionList.tsx",
  "components/ticker-detail/PositionTab.tsx",
  "components/PnlBreakdownModal.tsx",
];

/* ─── Behavioural pin ─────────────────────────────────────── */

const TICKER = "META";
const PUT_KEY = optionKey({ symbol: TICKER, expiry: "20260918", strike: 580, right: "P" });

function priceData(overrides: Partial<PriceData>): PriceData {
  return {
    symbol: "X", last: null, lastIsCalculated: false,
    bid: null, ask: null, bidSize: null, askSize: null,
    volume: null, high: null, low: null, open: null, close: null,
    week52High: null, week52Low: null, avgVolume: null,
    delta: null, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: "2026-08-26T16:00:00Z",
    ...overrides,
  };
}

/**
 * The seam the META card fell through: `last` sits OUTSIDE its own bid/ask, so
 * `resolveRealtimePrice` marks the leg at the 3.05 mid while a raw-`last` walk
 * marks it at the stale 2.00 trade. Over 40 short contracts that is -$12,200
 * against -$8,000 — two market values for one position, $4,200 apart.
 */
const OPTION_PRICES: Record<string, PriceData> = {
  [TICKER]: priceData({ symbol: TICKER, last: 577.39, bid: 577.3, ask: 577.5, close: 582.4 }),
  [PUT_KEY]: priceData({ symbol: PUT_KEY, last: 2.0, bid: 3.0, ask: 3.1, close: 3.2 }),
};

const SHORT_PUT = {
  id: 1,
  ticker: TICKER,
  structure: "Short Put $580",
  structure_type: "Short Put",
  risk_profile: "undefined",
  expiry: "2026-09-18",
  contracts: 40,
  direction: "SHORT",
  entry_cost: -12_017,
  max_risk: null,
  market_value: -12_120,
  ib_daily_pnl: null,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-08-26",
  legs: [{
    direction: "SHORT",
    contracts: 40,
    type: "Put",
    strike: 580,
    expiry: null,
    entry_cost: -12_017,
    avg_cost: 300.43,
    market_price: 3.03,
    market_value: -12_120,
  }],
} as unknown as PortfolioPosition;

const STOCK_PRICES: Record<string, PriceData> = {
  [TICKER]: priceData({ symbol: TICKER, last: 577.39, bid: 577.3, ask: 577.5, close: 582.4 }),
};

/** A SHORT equity: `pos.contracts` is a positive magnitude, so the market
 *  value only reads negative if the surface takes it from the shared resolver. */
const SHORT_STOCK = {
  id: 2,
  ticker: TICKER,
  structure: "Stock",
  structure_type: "Stock",
  risk_profile: "equity",
  expiry: "N/A",
  contracts: 300,
  direction: "SHORT",
  entry_cost: -170_000,
  max_risk: null,
  market_value: -172_000,
  ib_daily_pnl: null,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-08-26",
  legs: [{
    direction: "SHORT",
    contracts: 300,
    type: "Stock",
    strike: null,
    expiry: null,
    entry_cost: -170_000,
    avg_cost: 566.67,
    market_price: 573.33,
    market_value: -172_000,
  }],
} as unknown as PortfolioPosition;

const MV_FIXTURES = [
  { name: "META 40x short $580 put (last outside its own bid/ask)", pos: SHORT_PUT, prices: OPTION_PRICES },
  { name: "META 300 short shares", pos: SHORT_STOCK, prices: STOCK_PRICES },
];

/** The dollar figure a rendered node carries, as a signed number. Surfaces
 *  disagree on where the minus sign goes (`-$12,200` against `$-12,200`);
 *  that is a formatting choice, not a second market value. */
function money(text: string): number | null {
  const match = text.match(/(-?)\$(-?)([\d,]+)/);
  if (!match) return null;
  const sign = match[1] === "-" || match[2] === "-" ? -1 : 1;
  return sign * Number(match[3].replace(/,/g, ""));
}

/** The market value each surface publishes, read off the rendered DOM. */
const MV_READERS: Array<{
  rel: string;
  read: (pos: PortfolioPosition, prices: Record<string, PriceData>) => string;
}> = [
  {
    rel: "components/PositionTable.tsx",
    read: (pos, prices) => {
      render(React.createElement(PositionTable, { positions: [pos], prices }));
      const row = screen.getByText(pos.ticker).closest("tr")!;
      const headers = Array.from(row.closest("table")!.querySelectorAll("thead th"))
        .map((th) => th.textContent?.trim() ?? "");
      const idx = headers.findIndex((h) => h.startsWith("Market Value"));
      expect(idx, "PositionTable has no Market Value column").toBeGreaterThanOrEqual(0);
      return (row.querySelectorAll("td")[idx]?.textContent ?? "").trim();
    },
  },
  {
    rel: "components/mobile/MobilePositionList.tsx",
    read: (pos, prices) => {
      render(React.createElement(MobilePositionList, { positions: [pos], prices }));
      const card = screen.getByTestId(`mobile-position-${pos.ticker}`);
      const cell = within(card).getByText("MV").parentElement!;
      return (cell.textContent ?? "").replace("MV", "").trim();
    },
  },
  {
    rel: "components/ticker-detail/PositionTab.tsx",
    read: (pos, prices) => {
      render(React.createElement(PositionTab, { position: pos, prices }));
      const cell = screen.getByText("Market Value").parentElement!;
      return (cell.textContent ?? "").replace("Market Value", "").trim();
    },
  },
];

/* ─── Static tripwires ────────────────────────────────────── */

/** The verbatim line that shipped the 2026-08-26 META regression
 *  (`git show 8fdd116e^:web/components/mobile/MobilePositionList.tsx`). A
 *  matcher that claims to pin the leg walk MUST fire on this one. */
const HISTORICAL_MV_WALK =
  '      total += (leg.direction === "LONG" ? 1 : -1) * last * leg.contracts * 100;';

/** `x += <sign> * <price> * <contracts> * <multiplier>` in any spelling. */
const MV_ACCUMULATION = /\+=[^;\n]*\*[^;\n]*\bcontracts\b[^;\n]*\*\s*(100|[a-zA-Z]*[Mm]ultiplier)\b/;

/** The close-based daily walk legitimately accumulates per-leg CLOSES and does
 *  not produce a market value, so a line naming `close` is exempt. */
function isCloseWalk(line: string): boolean {
  return /\bclose\b/i.test(line);
}

/** The body of a declaration, from its opening brace to the MATCHING closing
 *  one. The byte-window slice this replaced could be pushed off its target by
 *  a doc comment or a new branch appearing above it. */
function declarationBody(source: string, decl: string): string {
  const start = source.indexOf(decl);
  if (start < 0) throw new Error(`declaration not found: ${decl}`);
  const open = source.indexOf("{", start);
  if (open < 0) throw new Error(`no body for: ${decl}`);
  let depth = 0;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(open, i + 1);
    }
  }
  throw new Error(`unterminated body for: ${decl}`);
}

/** Every `if (isSameDay(pos))` block inside `body`, brace-matched. */
function sameDayBlocks(body: string): string[] {
  const blocks: string[] = [];
  let from = 0;
  for (;;) {
    const at = body.indexOf("if (isSameDay(pos))", from);
    if (at < 0) return blocks;
    blocks.push(declarationBody(body.slice(at), "if (isSameDay(pos))"));
    from = at + 1;
  }
}

describe("market value has one source", () => {
  it("every pinned surface still exists", () => {
    // Without this, a rename turns every check below into a no-op: the old
    // version swallowed the read error and continued.
    const gone = POSITION_SURFACES.filter((rel) => !existsSync(join(WEB_ROOT, rel)));
    expect(gone).toEqual([]);
  });

  it("every surface publishes the shared resolver's market value", () => {
    const mismatches: string[] = [];
    for (const fixture of MV_FIXTURES) {
      const shared = resolveRealtimeMarketValue(fixture.pos, fixture.prices)
        ?? resolveMarketValue(fixture.pos);
      expect(shared, `${fixture.name}: no market value resolved`).not.toBeNull();
      const expected = money(fmtUsd(shared!));
      for (const surface of MV_READERS) {
        const rendered = surface.read(fixture.pos, fixture.prices);
        cleanup();
        if (money(rendered) !== expected) {
          mismatches.push(`${surface.rel} · ${fixture.name}: rendered ${rendered}, shared ${fmtUsd(shared!)}`);
        }
      }
    }
    expect(mismatches).toEqual([]);
  });

  it("the leg-walk matcher fires on the defect it was written for", () => {
    // A tripwire that does not trip on the historical defect pins nothing.
    expect(MV_ACCUMULATION.test(HISTORICAL_MV_WALK)).toBe(true);
    expect(isCloseWalk(HISTORICAL_MV_WALK)).toBe(false);
  });

  it("no position surface accumulates its own real-time market value", () => {
    const violations: string[] = [];
    for (const rel of POSITION_SURFACES) {
      const source = readFileSync(join(WEB_ROOT, rel), "utf-8");
      for (const line of source.split("\n")) {
        if (!isCloseWalk(line) && MV_ACCUMULATION.test(line)) {
          violations.push(`${rel}: ${line.trim()}`);
        }
      }
    }
    expect(violations).toEqual([]);
  });

  it("every position surface imports the shared resolver", () => {
    const missing = POSITION_SURFACES.filter((rel) => {
      const source = readFileSync(join(WEB_ROOT, rel), "utf-8");
      // Only surfaces that actually render a market value need it.
      const rendersMv = /resolveMarketValue|getPnlDollars|getTodayPnlDollars/.test(source);
      return rendersMv && !source.includes("resolveRealtimeMarketValue");
    });
    expect(missing).toEqual([]);
  });

  it("getTodayPnlDollars and the surfaces resolve through the same export", () => {
    const utils = readFileSync(join(WEB_ROOT, "lib/positionUtils.ts"), "utf-8");
    expect(utils).toContain("export function resolveRealtimeMarketValue");
    // Both same-day branches (stock and option) must derive their market value
    // from the shared walk, never from a locally re-derived one.
    const blocks = sameDayBlocks(declarationBody(utils, "export function getTodayPnlDollars"));
    expect(blocks.length).toBe(2);
    for (const block of blocks) {
      expect(block).toMatch(/computeRtMv|computeStockRtMv|resolveRealtimeMarketValue/);
    }
  });
});
