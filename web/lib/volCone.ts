/**
 * VOL CONE indicator — payload types + pure helpers for the vol-cone scanner tab.
 * Mirrors the GET /api/vol-cone contract: expiry-local ATM and 10% OTM wing
 * IVs versus that expiry's 90/10 cone. Spec: docs/indicators/vol-cone.md.
 */

export type VolConeRegime = "CHEAP_WINGS" | "CHEAP_ATM" | "RICH" | "NEUTRAL";

export interface VolConeSeriesPoint {
  date: string;
  spot: number;
  /** Nulls are preserved for chart gaps. */
  atm_iv: number | null;
  call_10_iv: number | null;
  put_10_iv: number | null;
}

export interface VolConeName {
  ticker: string;
  spot: number;
  expiry: string;
  month?: string | null;
  dte: number;
  atm_iv: number | null;
  call_10_iv: number | null;
  put_10_iv: number | null;
  call_10_strike: number | null;
  put_10_strike: number | null;
  p10: number | null;
  p90: number | null;
  atm_percentile: number | null;
  call_10_percentile: number | null;
  put_10_percentile: number | null;
  wing_score: number | null;
  regime: VolConeRegime;
  series: VolConeSeriesPoint[];
  /** True when this name's top point is a live sample of the open session.
   *  The live pass refreshes only the cheap tail plus the watchlist, so a
   *  live payload still carries names on their last completed close. */
  is_intraday?: boolean;
}

export interface VolConeData {
  scan_time: string | null;
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  source_as_of: string | null;
  /** True when the top point is a live sample of the open session rather
   *  than a completed close. The percentile is current; the distribution it
   *  is ranked against is still completed sessions only. */
  is_intraday?: boolean;
  /** How many names carry is_intraday; never more than count. */
  intraday_count?: number;
  count: number;
  hit_count: number;
  current: VolConeName | null;
  names: VolConeName[];
  hits: VolConeName[];
}

export interface VolConeChartRow {
  date: string;
  atm_iv: number | null;
  call_10_iv: number | null;
  put_10_iv: number | null;
}

/* ─── Formatting ─────────────────────────────────────── */

/** Decimal IV to one-decimal vol points: 0.3851 -> "38.5"; "---" null/non-finite. */
export function formatIvPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return (v * 100).toFixed(1);
}

/** Fraction -> one-decimal percent: 0.0556 -> "5.6%"; "---" null. */
export function formatPercentile(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${(v * 100).toFixed(1)}%`;
}

export function formatVolConeRegime(regime: VolConeRegime): string {
  return regime.replaceAll("_", " ");
}

/** Standard monthly label: "2026-09-18" -> "SEP 18". */
export function formatMonthlyExpiry(expiry: string | null | undefined): string {
  if (!expiry) return "---";
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(expiry);
  if (!match) return expiry;
  const months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
  const month = months[Number(match[2]) - 1];
  if (!month) return expiry;
  return `${month} ${Number(match[3])}`;
}

/* ─── Derivations ────────────────────────────────────── */

export function isHit(regime: VolConeRegime): boolean {
  return regime === "CHEAP_WINGS" || regime === "CHEAP_ATM";
}

export function volConeRegimeColor(regime: VolConeRegime): string {
  switch (regime) {
    case "CHEAP_WINGS":
      return "var(--positive)";
    case "CHEAP_ATM":
      return "var(--warning)";
    case "RICH":
      return "var(--negative)";
    case "NEUTRAL":
      return "var(--text-muted)";
  }
}

/* ─── Chart rows ─────────────────────────────────────── */

export function buildVolConeChartRows(
  series: ReadonlyArray<VolConeSeriesPoint>,
): VolConeChartRow[] {
  return series.map((point) => ({
    date: point.date,
    atm_iv: point.atm_iv,
    call_10_iv: point.call_10_iv,
    put_10_iv: point.put_10_iv,
  }));
}

/* ─── Analysis + trade href ──────────────────────────── */

export type VolConeTradeLeg = {
  action: "BUY";
  quantity: number;
  strike: number;
  right: "C" | "P";
};

export type VolConeTradeRecommendation = {
  kind: "strangle" | "straddle" | null;
  legs: VolConeTradeLeg[];
  href: string | null;
};

export type VolConeAnalysis = {
  regime: VolConeRegime;
  structureLabel: string;
  expectedMoveDollars: string;
  expectedMovePct: string;
  expectedMoveRange: string;
  coneGap: number;
  coneGapLabel: string;
  wingStrikes: { put: number | null; call: number | null };
  wingsSigma: number | null;
  thesis: string;
  winsIf: string;
  diesIf: string;
  notEdge: string;
  href: string | null;
};

const NO_TRADE: VolConeTradeRecommendation = { kind: null, legs: [], href: null };
const WING_OTM = 0.1;
const YEAR_DAYS = 365;

function isPositiveFinite(value: number | null | undefined): value is number {
  return value != null && Number.isFinite(value) && value > 0;
}

/** Listed strike step from spot: 0.5 below 25, 1 below 200, else 5. */
export function listedIncrement(spot: number): number {
  if (spot < 25) return 0.5;
  if (spot < 200) return 1;
  return 5;
}

/** Round a raw strike to listedIncrement(spot). */
export function snapListedStrike(strike: number, spot: number): number {
  const step = listedIncrement(spot);
  return Math.round(strike / step) * step;
}

export type VolConeExpectedMove = {
  dollars: number;
  fraction: number;
  lo: number;
  hi: number;
};

/** 1-sigma expected move. ATM IV is decimal, not percent. */
export function expectedMove(
  spot: number,
  atmIv: number,
  dte: number,
): VolConeExpectedMove | null {
  if (!Number.isFinite(spot) || !Number.isFinite(atmIv) || !Number.isFinite(dte)) return null;
  if (spot <= 0 || atmIv <= 0 || dte <= 0) return null;
  const fraction = atmIv * Math.sqrt(dte / YEAR_DAYS);
  if (!Number.isFinite(fraction) || fraction <= 0) return null;
  const dollars = spot * fraction;
  if (!Number.isFinite(dollars)) return null;
  return { dollars, fraction, lo: spot - dollars, hi: spot + dollars };
}

function serializeVolConeLegs(legs: readonly VolConeTradeLeg[]): string {
  return legs.map((leg) => `${leg.action}:${leg.quantity}x${leg.strike}${leg.right}`).join(",");
}

function volConeHrefFor(name: VolConeName, legs: readonly VolConeTradeLeg[]): string | null {
  const ticker = name.ticker.trim();
  const expiry = name.expiry.trim();
  if (!ticker || !expiry || legs.length === 0) return null;
  const params = new URLSearchParams();
  params.set("deck", "c");
  params.set("expiry", expiry);
  params.set("strikes", "100");
  params.set("src", "vol-cone");
  params.set("legs", serializeVolConeLegs(legs));
  return `/${encodeURIComponent(ticker)}?${params.toString()}`;
}

function buyLeg(strike: number, right: "C" | "P"): VolConeTradeLeg {
  return { action: "BUY", quantity: 1, strike, right };
}

export function recommendVolConeTrade(name: VolConeName): VolConeTradeRecommendation {
  if (!isPositiveFinite(name.spot)) return NO_TRADE;

  if (name.regime === "CHEAP_WINGS") {
    if (!isPositiveFinite(name.put_10_strike) || !isPositiveFinite(name.call_10_strike)) {
      return NO_TRADE;
    }
    const put = snapListedStrike(name.put_10_strike, name.spot);
    const call = snapListedStrike(name.call_10_strike, name.spot);
    if (!isPositiveFinite(put) || !isPositiveFinite(call)) return NO_TRADE;
    const legs = [buyLeg(put, "P"), buyLeg(call, "C")];
    return { kind: "strangle", legs, href: volConeHrefFor(name, legs) };
  }

  if (name.regime === "CHEAP_ATM") {
    const strike = snapListedStrike(name.spot, name.spot);
    if (!isPositiveFinite(strike)) return NO_TRADE;
    const legs = [buyLeg(strike, "C"), buyLeg(strike, "P")];
    return { kind: "straddle", legs, href: volConeHrefFor(name, legs) };
  }

  return NO_TRADE;
}

export function volConeOrderHref(name: VolConeName): string | null {
  return recommendVolConeTrade(name).href;
}

function analysisCopy(regime: VolConeRegime): {
  structureLabel: string;
  thesis: string;
  winsIf: string;
  diesIf: string;
} {
  switch (regime) {
    case "CHEAP_WINGS":
      return {
        structureLabel: "LONG 10% OTM STRANGLE",
        thesis:
          "Cheap insurance versus this expiry cone. The 10 percent OTM strangle is long wings only. Buy the listed 10 percent put and call. Do not sell this surface. It is not a stock call.",
        winsIf:
          "The name realizes more than the 1-sigma expected move into expiry, or implied vol lifts toward the cone floor.",
        diesIf:
          "Spot sits inside the 1-sigma band and vol stays crushed, so the debit decays.",
      };
    case "CHEAP_ATM":
      return {
        structureLabel: "LONG ATM STRADDLE",
        thesis:
          "Cheap insurance versus this expiry cone. The ATM straddle is long ATM only. Buy the listed ATM call and put. Do not sell this surface. It is not a stock call.",
        winsIf:
          "The name realizes more than the 1-sigma expected move into expiry, or implied vol lifts toward the cone floor.",
        diesIf:
          "Spot sits inside the 1-sigma band and vol stays crushed, so the debit decays.",
      };
    case "RICH":
      return {
        structureLabel: "NO TRADE",
        thesis:
          "This expiry cone is rich. Long wings or ATM would be buying expensive insurance, not a stock call.",
        winsIf: "No long-vol structure. A rich cone is a pass.",
        diesIf: "Buying this cone as insurance decays if vol stays rich and spot sits.",
      };
    case "NEUTRAL":
      return {
        structureLabel: "NO TRADE",
        thesis: "This expiry cone is not cheap enough for a defined-risk long-vol trade.",
        winsIf: "No long-vol structure until the cone prints cheap.",
        diesIf: "Forcing a long-vol trade here has no cone edge.",
      };
  }
}

function snappedTenPercentWings(name: VolConeName): { put: number | null; call: number | null } {
  if (!isPositiveFinite(name.spot)) return { put: null, call: null };
  return {
    put: isPositiveFinite(name.put_10_strike) ? snapListedStrike(name.put_10_strike, name.spot) : null,
    call: isPositiveFinite(name.call_10_strike) ? snapListedStrike(name.call_10_strike, name.spot) : null,
  };
}

function analysisWingStrikes(
  name: VolConeName,
  rec: VolConeTradeRecommendation,
): { put: number | null; call: number | null } {
  if (rec.kind === "straddle" || rec.kind === "strangle") {
    const put = rec.legs.find((leg) => leg.right === "P");
    const call = rec.legs.find((leg) => leg.right === "C");
    return { put: put?.strike ?? null, call: call?.strike ?? null };
  }
  return snappedTenPercentWings(name);
}

export function volConeTradeAriaLabel(name: VolConeName): string | null {
  const rec = recommendVolConeTrade(name);
  if (rec.kind === "strangle") return `Open ${name.ticker} long 10% OTM strangle`;
  if (rec.kind === "straddle") return `Open ${name.ticker} long ATM straddle`;
  return null;
}

export function buildVolConeAnalysis(name: VolConeName): VolConeAnalysis {
  const rec = recommendVolConeTrade(name);
  const move = expectedMove(name.spot, name.atm_iv ?? Number.NaN, name.dte);
  const copy = analysisCopy(name.regime);
  const coneGap =
    name.p10 != null && name.atm_iv != null && Number.isFinite(name.p10) && Number.isFinite(name.atm_iv)
      ? (name.p10 - name.atm_iv) * 100
      : Number.NaN;
  return {
    regime: name.regime,
    structureLabel: copy.structureLabel,
    expectedMoveDollars: move ? `$${Math.round(move.dollars)}` : "---",
    expectedMovePct: move ? `${Math.round(move.fraction * 100)}%` : "---",
    expectedMoveRange: move ? `$${Math.round(move.lo)} to $${Math.round(move.hi)}` : "---",
    coneGap,
    coneGapLabel: Number.isFinite(coneGap) ? `${coneGap.toFixed(1)} vol pts` : "---",
    wingStrikes: analysisWingStrikes(name, rec),
    wingsSigma: move && move.fraction > 0 ? WING_OTM / move.fraction : null,
    thesis: copy.thesis,
    winsIf: copy.winsIf,
    diesIf: copy.diesIf,
    notEdge: "This is not a dark-pool edge. It is a cheap-vol rank on this monthly cone.",
    href: rec.href,
  };
}
