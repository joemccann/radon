/**
 * Working-order fill window vs US equity RTH (09:30-16:00 ET).
 *
 * Options and option combos never fill after equity RTH, even when
 * outsideRth is true. Stocks fill after RTH only when outsideRth is true.
 * Futures can fill outside equity RTH. Missing outsideRth is false.
 */

import { MarketState, marketStateAt } from "@/lib/useMarketHours";
import type { OpenOrderDisplayRow } from "@/lib/openOrderCombos";

/** Market state for the chip: holiday- and early-close-aware via the shared
 * calendar (R-336: past a 13:00 half-day close the session reads EXTENDED). */
export function chipMarketState(now: Date): MarketState {
  return marketStateAt(now);
}

export type SessionEligibility = "extended" | "rth-only";
export type SessionChipLabel = "EXT LIVE" | "EXT" | "RTH" | "EXPIRES" | "NEXT RTH";
export type SessionChipTone = "extended" | "expires" | "rth";

export type SessionOrderLike = {
  tif?: string | null;
  outsideRth?: boolean | null;
  contract?: {
    secType?: string | null;
  } | null;
};

export type OrderSession = {
  eligibility: SessionEligibility;
  label: SessionChipLabel;
  tone: SessionChipTone;
  hint: string;
  marketState: MarketState;
};

const FILL_AFTER = "after 16:00 ET";

function secTypeOf(order: SessionOrderLike): string {
  return String(order.contract?.secType ?? "").toUpperCase();
}

export function sessionEligibility(order: SessionOrderLike): SessionEligibility {
  const secType = secTypeOf(order);
  if (secType === "OPT" || secType === "BAG") return "rth-only";
  // FUT/FOP used to return "extended" UNCONDITIONALLY, where the STK rule
  // below correctly requires the flag. `/api/orders/place` auto-derives
  // `outsideRth` as `body.outsideRth ?? getMarketStateFromDate() !== "open"`,
  // so a futures order entered at 11:00 ET is transmitted with `false` — and
  // the chip still read EXT LIVE at 18:00 ET for an order inert until the
  // next morning. R-338.
  if (order.outsideRth === true && (secType === "FUT" || secType === "FOP")) {
    return "extended";
  }
  if (order.outsideRth === true && secType === "STK") return "extended";
  return "rth-only";
}

/** TIFs that do NOT survive the session close. `MIXED` is what a grouped
 * combo carries when its legs disagree, so it contains at least one DAY leg:
 * that leg is cancelled at the close and any GTC sibling rests on alone,
 * which for a vertical means a naked short. Falling through to the GTC branch
 * labelled it `NEXT RTH` — "survives to the next session". R-337. */
const EXPIRING_TIFS = new Set(["DAY", "MIXED"]);

function chipLabel(
  eligibility: SessionEligibility,
  tif: string,
  marketState: MarketState,
): SessionChipLabel {
  if (eligibility === "extended") {
    return marketState === MarketState.EXTENDED ? "EXT LIVE" : "EXT";
  }
  if (marketState === MarketState.OPEN) return "RTH";
  if (EXPIRING_TIFS.has(tif)) return "EXPIRES";
  return "NEXT RTH";
}

function chipTone(
  eligibility: SessionEligibility,
  label: SessionChipLabel,
  tif: string,
): SessionChipTone {
  if (label === "EXPIRES") return "expires";
  // The extended branch used to return BEFORE the TIF check, so an
  // extended-eligible DAY order could never carry the expiry warning while
  // the identical RTH-only DAY order did — an expiry signal gated on a flag
  // unrelated to expiry. IB cancels it at the 20:00 ET extended close. R-367.
  if (eligibility === "extended" && EXPIRING_TIFS.has(tif)) return "expires";
  if (eligibility === "extended") return "extended";
  return "rth";
}

const EXTENDED_CLOSE = "20:00 ET";

function hintFor(eligibility: SessionEligibility, tif: string): string {
  if (eligibility === "extended") {
    return EXPIRING_TIFS.has(tif)
      ? `Can fill ${FILL_AFTER}. Cancelled at the ${EXTENDED_CLOSE} extended close.`
      : `Can fill ${FILL_AFTER}.`;
  }
  return `Will not fill ${FILL_AFTER}.`;
}

export function classifyOrderSession(
  order: SessionOrderLike,
  now: Date = new Date(),
): OrderSession {
  const eligibility = sessionEligibility(order);
  const marketState = chipMarketState(now);
  const tif = String(order.tif ?? "").toUpperCase();
  const label = chipLabel(eligibility, tif, marketState);
  return {
    eligibility,
    label,
    tone: chipTone(eligibility, label, tif),
    hint: hintFor(eligibility, tif),
    marketState,
  };
}

export function classifyDisplayRowSession(
  row: OpenOrderDisplayRow,
  now: Date = new Date(),
): OrderSession {
  if (row.kind === "combo") {
    return classifyOrderSession(
      {
        tif: row.tif,
        outsideRth: row.orders.some((leg) => leg.outsideRth === true),
        contract: { secType: "BAG" },
      },
      now,
    );
  }
  return classifyOrderSession(row.order, now);
}

/**
 * True when the order's EXTENDED fill window is live RIGHT NOW: the order is
 * extended-eligible and the market is currently in an extended session
 * (pre-market or after hours). IB holds extended-eligible equity orders in
 * `PreSubmitted` while they are live and fillable in that session, so the
 * status mapper needs this to label them Working instead of Queued.
 */
export function isExtendedFillLive(session: OrderSession): boolean {
  return (
    session.eligibility === "extended"
    && session.marketState === MarketState.EXTENDED
  );
}

export type SessionWindowCounts = {
  rth: number;
  ext: number;
};

export function summarizeSessionWindows(
  rows: readonly OpenOrderDisplayRow[],
  now: Date = new Date(),
): SessionWindowCounts {
  let rth = 0;
  let ext = 0;
  for (const row of rows) {
    if (classifyDisplayRowSession(row, now).eligibility === "extended") ext += 1;
    else rth += 1;
  }
  return { rth, ext };
}
