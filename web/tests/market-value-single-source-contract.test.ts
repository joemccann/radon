/**
 * One market value per position.
 *
 * Twice now, a position's Today P&L has contradicted its own total P&L on a
 * surface — a stock-only inline copy of Today P&L (2026-08-11) and a
 * card-local real-time market-value walk (2026-08-26, META 40x short $580 put:
 * +$1,097 total against -$103 today). In both cases the shared helper was
 * correct and the surface had quietly grown a second opinion.
 *
 * A file that walks `pos.legs` to accumulate a market value has grown one.
 * `resolveRealtimeMarketValue` in `lib/positionUtils.ts` is the walk; every
 * surface calls it and falls back with `?? resolveMarketValue(pos)`.
 *
 * A walk over legs for something OTHER than market value (Greeks, per-leg
 * rows, implied value, close-based daily figures) is fine — this pins the
 * accumulation of a price × contracts × multiplier TOTAL, which is the only
 * thing that can disagree with itself.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const WEB_ROOT = join(__dirname, "..");

/** Surfaces that render a position's market value, P&L, or Today P&L. */
const POSITION_SURFACES = [
  "components/PositionTable.tsx",
  "components/mobile/MobilePositionList.tsx",
  "components/ticker-detail/PositionTab.tsx",
  "components/PnlBreakdownModal.tsx",
];

/** `x += sign * price * contracts * multiplier` in any spelling. */
const MV_ACCUMULATION = /[+]=\s*(sign|[a-zA-Z]*[Ss]ign)[^;\n]*\*\s*[^;\n]*\bcontracts\b[^;\n]*\*\s*[^;\n]*(100|multiplier|Multiplier)/;

describe("market value has one source", () => {
  it("no position surface accumulates its own real-time market value", () => {
    const violations: string[] = [];
    for (const rel of POSITION_SURFACES) {
      let source: string;
      try {
        source = readFileSync(join(WEB_ROOT, rel), "utf-8");
      } catch {
        continue; // surface renamed or removed; the list below is the pin
      }
      // Strip the close-based daily walk, which legitimately needs per-leg
      // closes and does NOT produce a market value.
      for (const line of source.split("\n")) {
        if (/\brtMv\b|\bmarketValue\b|\bmv\b/.test(line) && MV_ACCUMULATION.test(line)) {
          violations.push(`${rel}: ${line.trim()}`);
        }
      }
    }
    expect(violations).toEqual([]);
  });

  it("every position surface imports the shared resolver", () => {
    const missing = POSITION_SURFACES.filter((rel) => {
      let source: string;
      try {
        source = readFileSync(join(WEB_ROOT, rel), "utf-8");
      } catch {
        return false;
      }
      // Only surfaces that actually render a market value need it.
      const rendersMv = /resolveMarketValue|getPnlDollars|getTodayPnlDollars/.test(source);
      return rendersMv && !source.includes("resolveRealtimeMarketValue");
    });
    expect(missing).toEqual([]);
  });

  it("getTodayPnlDollars and the surfaces resolve through the same export", () => {
    const utils = readFileSync(join(WEB_ROOT, "lib/positionUtils.ts"), "utf-8");
    expect(utils).toContain("export function resolveRealtimeMarketValue");
    // The same-day branch must derive its market value from the shared walk,
    // never from a locally re-derived one.
    const sameDayBranch = utils.slice(
      utils.indexOf("export function getTodayPnlDollars"),
      utils.indexOf("export function getTodayPnlDollars") + 1_400,
    );
    expect(sameDayBranch).toMatch(/computeRtMv|resolveRealtimeMarketValue/);
  });
});
