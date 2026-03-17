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
  history?: Array<Pick<CriHistoryEntry, "cor1m">>;
};

type ResolveRegimeStripLiveStateInput = {
  prices: Record<string, PriceData>;
  data?: Partial<RegimeStripData> | null;
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

export function resolveRegimeStripLiveState({
  prices,
  data,
}: ResolveRegimeStripLiveStateInput): RegimeStripLiveState {
  const liveVix = prices.VIX?.last ?? null;
  const liveVvix = prices.VVIX?.last ?? null;
  const liveSpy = prices.SPY?.last ?? null;
  const liveCor1m = prices.COR1M?.last ?? null;

  const vixClose = prices.VIX?.close ?? data?.vix ?? null;
  const vvixClose = prices.VVIX?.close ?? data?.vvix ?? null;
  const spyClose = prices.SPY?.close ?? data?.spy ?? null;

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
