/**
 * Working-order fill window vs US equity RTH (09:30-16:00 ET).
 *
 * Options and option combos never fill after equity RTH, even when
 * outsideRth is true. Stocks fill after RTH only when outsideRth is true.
 * Futures can fill outside equity RTH. Missing outsideRth is false.
 */

import { MarketState, marketStateAt } from "@/lib/useMarketHours";
import { isHolidayTableCovering, isUsTradingDay } from "@/lib/serviceHealthWindows";
import type { OpenOrderDisplayRow } from "@/lib/openOrderCombos";

/** US equity early closes (13:00 ET), from the NYSE published calendar.
 *
 * `marketStateAt` models neither holidays nor early closes — it is a weekend
 * test plus fixed 09:30-16:00 minutes — yet the chip states a definitive fill
 * claim off it. At 14:30 ET on an early-close day it read RTH, every resting
 * DAY order was presented as still fillable, and the EXPIRES warning was
 * suppressed. Full closures come from the shared holiday table
 * (`scripts/config/market_holidays.json` via `isUsTradingDay`); early closes
 * have no shared table, so they are listed here with an explicit coverage
 * bound — outside it the chip degrades rather than guesses. R-336.
 */
const EQUITY_EARLY_CLOSE_MIN: Record<string, number> = {
  "2026-07-02": 13 * 60,
  "2026-11-27": 13 * 60,
  "2026-12-24": 13 * 60,
  "2027-07-02": 13 * 60,
  "2027-11-26": 13 * 60,
  "2027-12-23": 13 * 60,
};

function etDateParts(now: Date): { iso: string; minutes: number } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(now);
  const value = (type: string) => parts.find((p) => p.type === type)?.value ?? "00";
  return {
    iso: `${value("year")}-${value("month")}-${value("day")}`,
    minutes: Number(value("hour")) % 24 * 60 + Number(value("minute")),
  };
}

/** Market state for the chip: holiday- and early-close-aware. */
export function chipMarketState(now: Date): MarketState {
  const base = marketStateAt(now);
  const { iso, minutes } = etDateParts(now);

  // Outside the holiday table there is nothing to check against, so keep the
  // base verdict rather than inventing one.
  if (!isHolidayTableCovering(iso)) return base;

  // A full-closure weekday is CLOSED all day, not OPEN or EXTENDED.
  if (!isUsTradingDay(iso)) return MarketState.CLOSED;

  const earlyClose = EQUITY_EARLY_CLOSE_MIN[iso];
  if (earlyClose != null && base === MarketState.OPEN && minutes > earlyClose) {
    // Past the early close the session is over; extended hours run to 20:00.
    return MarketState.EXTENDED;
  }
  return base;
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
