import { lastCompletedSessionDate } from "./marketSession";
import type { PriceData } from "./pricesProtocol";
import type { CriData, CriHistoryEntry } from "./useRegime";

type RegimeStripData = Pick<
  CriData,
  | "vix"
  | "vvix"
  | "spy"
  | "cor1m"
  | "cor1m_previous_close"
  | "cor1m_5d_change"
  | "vvix_vix_ratio"
  | "spx_100d_ma"
  | "spx_distance_pct"
> & {
  history?: Array<Partial<CriHistoryEntry>>;
};

type ResolveRegimeStripLiveStateInput = {
  prices: Record<string, PriceData>;
  data?: Partial<RegimeStripData> | null;
  marketOpen?: boolean;
  /** Session whose close the day change is measured against (ET, YYYY-MM-DD).
   *  Defaults to the last COMPLETED session — intraday that is yesterday. */
  sessionDate?: string;
};

export type RegimeStripLiveState = {
  liveVix: number | null;
  liveVvix: number | null;
  liveSpy: number | null;
  liveCor1m: number | null;
  hasLiveVix: boolean;
  hasLiveVvix: boolean;
  hasLiveSpy: boolean;
  hasLiveCor1m: boolean;
  vixValue: number | null;
  vvixValue: number | null;
  spyValue: number | null;
  cor1mValue: number | null;
  vixClose: number | null;
  vvixClose: number | null;
  spyClose: number | null;
  cor1mPreviousClose: number | null;
  corr5dChange: number | null;
  vvixVixRatio: number | null;
  spxDistancePct: number | null;
};

/**
 * COR1M above this level is the "panic herding" leg of the CRI crash trigger.
 * The panel label renders from this same constant so the displayed threshold
 * and the one the trigger actually uses cannot drift apart.
 */
export const CRASH_TRIGGER_CORRELATION_THRESHOLD = 60;

export function resolveCrashTriggerState(args: {
  liveCorrelation: boolean;
  correlation: number | null;
  cachedCorrelationMet: boolean;
  spxBelowMa: boolean;
  realizedVolMet: boolean;
}): { correlationMet: boolean; triggered: boolean } {
  const correlationMet = args.liveCorrelation && args.correlation != null
    ? args.correlation > CRASH_TRIGGER_CORRELATION_THRESHOLD
    : args.cachedCorrelationMet;
  return {
    correlationMet,
    triggered: args.spxBelowMa && args.realizedVolMet && correlationMet,
  };
}

type CriReading = {
  score: number;
  level: string;
  components: { vix: number; vvix: number; correlation: number; momentum: number };
};

/**
 * Decide whether there is a crash-risk reading to draw at all.
 *
 * The panel used to fall back to a literal `{score: 0, level: "LOW"}` when the
 * payload carried no `cri`. Zero is not a neutral placeholder: it is the
 * calmest value the hero, the level badge and all four `ComponentBar`s can
 * show, and it is also a legal real reading, so a dead feed and a quiet market
 * rendered identically. An absent reading is now absent. R-200.
 */
export function resolveCriDisplay(
  data: { missing?: boolean; cri?: CriReading | null } | null | undefined,
  liveCri: CriReading | null | undefined,
): { available: boolean; cri: CriReading | null } {
  if (liveCri) return { available: true, cri: liveCri };
  const cached = data?.cri ?? null;
  if (!cached || data?.missing) return { available: false, cri: null };
  return { available: true, cri: cached };
}

/**
 * Widest gap (calendar days) tolerated between the session a day change is
 * anchored to and the newest daily close the cached payload actually carries.
 * Covers a holiday-extended weekend; beyond that the scan is too old to price
 * today's move against and the baseline is withheld instead of invented.
 */
const MAX_PREVIOUS_CLOSE_GAP_DAYS = 7;

function calendarDaysBetween(from: string, to: string): number {
  const start = Date.parse(`${from}T00:00:00Z`);
  const end = Date.parse(`${to}T00:00:00Z`);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return Number.POSITIVE_INFINITY;
  return Math.abs(end - start) / 86_400_000;
}

/**
 * Previous-session close for one strip series, read off the CRI payload's own
 * daily history and anchored to `sessionDate`.
 *
 * A day change is `live − previous close`, and neither substitute the panel
 * used to reach for is that number:
 *  - `data.vix` is the scan's SPOT reading (the value when the scan ran), so a
 *    cached payload from an earlier session renders a fabricated double-digit
 *    move (2026-08-28: VIX shown -11.35% against a 16.65 baseline while it was
 *    +1.65% on the day).
 *  - `prices.VIX.close` is IB's tick-9 close cached in the relay's memory for
 *    the life of the process, so it can be sessions behind.
 * History rows carry a date, so a real close can be picked and a payload too
 * old to anchor today's session can be rejected outright. R-200: an absent
 * baseline renders absent rather than as a wrong number.
 */
export function resolvePreviousSessionClose(
  history: Array<Partial<CriHistoryEntry>> | undefined,
  series: "vix" | "vvix" | "spy",
  sessionDate: string,
): number | null {
  if (!history?.length) return null;
  for (let index = history.length - 1; index >= 0; index -= 1) {
    const entry = history[index];
    const date = entry?.date;
    if (typeof date !== "string" || date > sessionDate) continue;
    if (calendarDaysBetween(date, sessionDate) > MAX_PREVIOUS_CLOSE_GAP_DAYS) return null;
    const close = entry[series];
    return typeof close === "number" && Number.isFinite(close) && close > 0 ? close : null;
  }
  return null;
}

export function resolveRegimeStripLiveState({
  prices,
  data,
  marketOpen = true,
  sessionDate = lastCompletedSessionDate(),
}: ResolveRegimeStripLiveStateInput): RegimeStripLiveState {
  const liveVix = marketOpen ? prices.VIX?.last ?? null : null;
  const liveVvix = marketOpen ? prices.VVIX?.last ?? null : null;
  const liveSpy = marketOpen ? prices.SPY?.last ?? null : null;
  const liveCor1m = marketOpen ? prices.COR1M?.last ?? null : null;

  // The relay's tick-9 close is the fallback ONLY when the payload carries no
  // daily history to anchor against. Once history exists it is authoritative:
  // a history that cannot reach `sessionDate` means the cached scan is too old
  // to price today's move, and no baseline beats a wrong one.
  const previousClose = (series: "vix" | "vvix" | "spy", relayClose: number | null | undefined) =>
    data?.history?.length
      ? resolvePreviousSessionClose(data.history, series, sessionDate)
      : relayClose ?? null;
  const vixClose = previousClose("vix", prices.VIX?.close);
  const vvixClose = previousClose("vvix", prices.VVIX?.close);
  const spyClose = previousClose("spy", prices.SPY?.close);

  const vixValue = liveVix ?? data?.vix ?? null;
  const vvixValue = liveVvix ?? data?.vvix ?? null;
  const spyValue = liveSpy ?? data?.spy ?? null;
  const cor1mValue = liveCor1m ?? data?.cor1m ?? null;

  const lastHistoryCor1m = data?.history && data.history.length > 0
    ? data.history[data.history.length - 1]?.cor1m ?? null
    : null;
  const cor1mPreviousClose = data?.cor1m_previous_close ?? lastHistoryCor1m ?? null;

  const vvixVixRatio =
    vixValue != null && vvixValue != null && vixValue > 0 ? vvixValue / vixValue : data?.vvix_vix_ratio ?? null;
  const ma = data?.spx_100d_ma ?? null;
  const spxDistancePct = ma && ma > 0 && spyValue != null
    ? ((spyValue / ma) - 1) * 100
    : data?.spx_distance_pct ?? null;

  return {
    liveVix,
    liveVvix,
    liveSpy,
    liveCor1m,
    hasLiveVix: liveVix != null,
    hasLiveVvix: liveVvix != null,
    hasLiveSpy: liveSpy != null,
    hasLiveCor1m: liveCor1m != null,
    vixValue,
    vvixValue,
    spyValue,
    cor1mValue,
    vixClose,
    vvixClose,
    spyClose,
    cor1mPreviousClose,
    corr5dChange: data?.cor1m_5d_change ?? null,
    vvixVixRatio,
    spxDistancePct,
  };
}
