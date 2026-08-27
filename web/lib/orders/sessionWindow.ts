/**
 * Working-order fill window vs US equity RTH (09:30-16:00 ET).
 *
 * Options and option combos never fill after equity RTH, even when
 * outsideRth is true. Stocks fill after RTH only when outsideRth is true.
 * Futures can fill outside equity RTH. Missing outsideRth is false.
 */

import { MarketState, marketStateAt } from "@/lib/useMarketHours";
import type { OpenOrderDisplayRow } from "@/lib/openOrderCombos";

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
  if (secType === "FUT" || secType === "FOP") return "extended";
  if (order.outsideRth === true && secType === "STK") return "extended";
  return "rth-only";
}

function chipLabel(
  eligibility: SessionEligibility,
  tif: string,
  marketState: MarketState,
): SessionChipLabel {
  if (eligibility === "extended") {
    return marketState === MarketState.EXTENDED ? "EXT LIVE" : "EXT";
  }
  if (marketState === MarketState.OPEN) return "RTH";
  if (tif === "DAY") return "EXPIRES";
  return "NEXT RTH";
}

function chipTone(eligibility: SessionEligibility, label: SessionChipLabel): SessionChipTone {
  if (label === "EXPIRES") return "expires";
  if (eligibility === "extended") return "extended";
  return "rth";
}

function hintFor(eligibility: SessionEligibility): string {
  if (eligibility === "extended") return `Can fill ${FILL_AFTER}.`;
  return `Will not fill ${FILL_AFTER}.`;
}

export function classifyOrderSession(
  order: SessionOrderLike,
  now: Date = new Date(),
): OrderSession {
  const eligibility = sessionEligibility(order);
  const marketState = marketStateAt(now);
  const tif = String(order.tif ?? "").toUpperCase();
  const label = chipLabel(eligibility, tif, marketState);
  return {
    eligibility,
    label,
    tone: chipTone(eligibility, label),
    hint: hintFor(eligibility),
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
