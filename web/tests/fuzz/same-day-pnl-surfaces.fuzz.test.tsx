/**
 * @vitest-environment jsdom
 *
 * The same-day identity, asserted where it is actually published.
 *
 * `same-day-pnl.fuzz.test.ts` proves the identity inside positionUtils. That
 * is not where it has broken. Both regressions were SURFACE-local: a stock-only
 * inline copy of Today P&L (2026-08-11) and a card-local real-time market-value
 * walk (2026-08-26). The lib was self-consistent through both; the pixels were
 * not. So this suite drives the rendered components and reads the numbers off
 * the DOM, exactly as the operator does.
 *
 * Quotes are generated to straddle the seam that split the two walks: a `last`
 * that sits outside its own bid/ask, a spread wide enough for
 * resolveRealtimePrice to prefer the mid, a leg expiry that moves the price
 * key, and a missing quote that drops one walk to `market_price`.
 *
 * Run count is deliberately modest — each run mounts two React trees. The
 * seam is narrow and 120 draws saturate it; breadth lives in the lib suite.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import fc from "fast-check";

import PositionTable from "@/components/PositionTable";
import MobilePositionList from "@/components/mobile/MobilePositionList";
import { optionKey } from "@/lib/pricesProtocol";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioPosition } from "@/lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

afterEach(cleanup);

const FC_OPTS = process.env.RADON_FUZZ_RANDOM === "1"
  ? { numRuns: 120 }
  : { numRuns: 120, seed: 42 };

function todayET(): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const get = (type: string) => parts.find((p) => p.type === type)!.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

const TODAY = todayET();
const TICKER = "META";

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

/** Quotes chosen to land on both sides of resolveRealtimePrice's mid rule:
 *  `last` inside a tight spread (last wins), `last` outside the spread or
 *  inside a wide one (mid wins), and no quote at all (market_price wins). */
const arbQuote = fc.oneof(
  // Tight and honest — last wins on every path.
  fc.record({ kind: fc.constant("tight" as const), mid: fc.double({ min: 1, max: 20, noNaN: true }) }),
  // Wide spread, last adrift — resolveRealtimePrice swaps in the mid.
  fc.record({ kind: fc.constant("wide" as const), mid: fc.double({ min: 1, max: 20, noNaN: true }) }),
  // Last outside its own bid/ask — clearly stale.
  fc.record({ kind: fc.constant("stale" as const), mid: fc.double({ min: 1, max: 20, noNaN: true }) }),
  // No quote — both walks must fall back to the same place.
  fc.record({ kind: fc.constant("absent" as const), mid: fc.double({ min: 1, max: 20, noNaN: true }) }),
);

type Quote = { kind: "tight" | "wide" | "stale" | "absent"; mid: number };

function quoteToPrice(key: string, q: Quote): PriceData | null {
  const mid = Number(q.mid.toFixed(2));
  switch (q.kind) {
    case "tight":
      return priceData({ symbol: key, last: mid, bid: mid - 0.01, ask: mid + 0.01, close: mid * 1.1 });
    case "wide":
      return priceData({ symbol: key, last: mid * 0.9, bid: mid * 0.8, ask: mid * 1.2, close: mid * 1.1 });
    case "stale":
      return priceData({ symbol: key, last: mid * 0.5, bid: mid - 0.05, ask: mid + 0.05, close: mid * 1.1 });
    case "absent":
      return null;
  }
}

const arbPosition = fc.record({
  right: fc.constantFrom("Call" as const, "Put" as const),
  direction: fc.constantFrom("LONG" as const, "SHORT" as const),
  strike: fc.constantFrom(560, 580, 600),
  contracts: fc.integer({ min: 1, max: 60 }),
  entryPerContract: fc.double({ min: 0.5, max: 20, noNaN: true }),
  quote: arbQuote,
  // A leg expiry that disagrees with the position's moves the option key —
  // one walk finds the quote, the other does not.
  legExpiryDiffers: fc.boolean(),
  // A stale broker figure that a same-day position must never read.
  ibDailyPnl: fc.option(fc.double({ min: -5_000, max: 5_000, noNaN: true }), { nil: null }),
  syncedPerContract: fc.double({ min: 0.5, max: 20, noNaN: true }),
});

function build(spec: fc.Value<typeof arbPosition> extends never ? never : {
  right: "Call" | "Put"; direction: "LONG" | "SHORT"; strike: number; contracts: number;
  entryPerContract: number; quote: Quote; legExpiryDiffers: boolean;
  ibDailyPnl: number | null; syncedPerContract: number;
}): { pos: PortfolioPosition; prices: Record<string, PriceData> } {
  const posExpiry = "2026-09-18";
  const legExpiry = spec.legExpiryDiffers ? "2026-10-16" : null;
  const key = optionKey({
    symbol: TICKER,
    expiry: (legExpiry ?? posExpiry).replace(/-/g, ""),
    strike: spec.strike,
    right: spec.right === "Call" ? "C" : "P",
  });
  const prices: Record<string, PriceData> = {
    [TICKER]: priceData({ symbol: TICKER, last: 577.39, close: 582.4 }),
  };
  const quote = quoteToPrice(key, spec.quote);
  if (quote) prices[key] = quote;

  const sign = spec.direction === "LONG" ? 1 : -1;
  const notional = (v: number) => sign * Number(v.toFixed(2)) * spec.contracts * 100;
  const pos = {
    id: 1,
    ticker: TICKER,
    structure: `${spec.direction === "LONG" ? "Long" : "Short"} ${spec.right} $${spec.strike}`,
    structure_type: spec.direction === "LONG" ? `Long ${spec.right}` : `Short ${spec.right}`,
    risk_profile: "undefined",
    expiry: posExpiry,
    contracts: spec.contracts,
    direction: spec.direction,
    entry_cost: notional(spec.entryPerContract),
    max_risk: null,
    market_value: notional(spec.syncedPerContract),
    ib_daily_pnl: spec.ibDailyPnl,
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: TODAY,
    legs: [{
      direction: spec.direction,
      contracts: spec.contracts,
      type: spec.right,
      strike: spec.strike,
      expiry: legExpiry,
      entry_cost: notional(spec.entryPerContract),
      avg_cost: Number(spec.entryPerContract.toFixed(2)) * 100,
      market_price: Number(spec.syncedPerContract.toFixed(2)),
      market_value: notional(spec.syncedPerContract),
    }],
  } as unknown as PortfolioPosition;
  return { pos, prices };
}

/** The dollar figures a rendered node carries, normalised so "-$103" and
 *  "-$103" compare regardless of the surface's own prefix conventions. */
function money(text: string): string | null {
  const match = text.match(/-?\$[\d,]+/);
  return match ? match[0].replace("+", "") : null;
}

describe("same-day P&L identity, as rendered (property)", () => {
  it("S1 — the mobile card's Today equals the P&L it headlines", () => {
    fc.assert(
      fc.property(arbPosition, (spec) => {
        const { pos, prices } = build(spec);
        const view = render(<MobilePositionList positions={[pos]} prices={prices} />);
        try {
          const card = screen.getByTestId(`mobile-position-${TICKER}`);
          const today = money(within(card).getByText("Today").parentElement!.textContent!);
          const headline = money(within(card).getByTestId("mobile-position-pnl").textContent!);
          expect(today).toBe(headline);
        } finally {
          view.unmount();
        }
      }),
      FC_OPTS,
    );
  });

  it("S2 — the desktop row's Today P&L equals its P&L column", () => {
    fc.assert(
      fc.property(arbPosition, (spec) => {
        const { pos, prices } = build(spec);
        const view = render(<PositionTable positions={[pos]} prices={prices} />);
        try {
          const row = screen.getByText(TICKER).closest("tr")!;
          const cells = Array.from(row.querySelectorAll("td")).map((td) => td.textContent ?? "");
          const headerCells = Array.from(
            row.closest("table")!.querySelectorAll("thead th"),
          ).map((th) => th.textContent?.trim().toUpperCase() ?? "");
          const todayIdx = headerCells.findIndex((h) => h.startsWith("TODAY"));
          const pnlIdx = headerCells.findIndex((h) => h === "P&L");
          if (todayIdx < 0 || pnlIdx < 0) return;
          expect(money(cells[todayIdx] ?? "")).toBe(money(cells[pnlIdx] ?? ""));
        } finally {
          view.unmount();
        }
      }),
      FC_OPTS,
    );
  });

  it("S3 — desktop and mobile publish the same market value for one position", () => {
    fc.assert(
      fc.property(arbPosition, (spec) => {
        const { pos, prices } = build(spec);
        const desktop = render(<PositionTable positions={[pos]} prices={prices} />);
        let desktopMv: string | null = null;
        try {
          const row = screen.getByText(TICKER).closest("tr")!;
          const cells = Array.from(row.querySelectorAll("td")).map((td) => td.textContent ?? "");
          const headerCells = Array.from(
            row.closest("table")!.querySelectorAll("thead th"),
          ).map((th) => th.textContent?.trim().toUpperCase() ?? "");
          const mvIdx = headerCells.findIndex((h) => h.startsWith("MARKET VALUE") || h === "MV");
          if (mvIdx < 0) return;
          desktopMv = money(cells[mvIdx] ?? "");
        } finally {
          desktop.unmount();
        }

        const mobile = render(<MobilePositionList positions={[pos]} prices={prices} />);
        try {
          const card = screen.getByTestId(`mobile-position-${TICKER}`);
          const mobileMv = money(within(card).getByText("MV").parentElement!.textContent!);
          expect(mobileMv).toBe(desktopMv);
        } finally {
          mobile.unmount();
        }
      }),
      FC_OPTS,
    );
  });
});
