import type { PriceData } from "@/lib/pricesProtocol";
import { fmtPrice } from "@/lib/positionUtils";

export type QuoteTelemetryFieldKey =
  | "bid"
  | "mid"
  | "ask"
  | "spread"
  | "last"
  | "volume"
  | "high"
  | "low"
  | "day";

type QuoteTone = "positive" | "negative" | null;
type QuoteTrend = "up" | "down" | null;

export type QuoteTelemetryField = {
  label: string;
  value: string;
  tone: QuoteTone;
  trend: QuoteTrend;
};

export type QuoteTelemetryModel = Record<QuoteTelemetryFieldKey, QuoteTelemetryField>;

/**
 * After-hours fallback for the UNDERLYING stock, sourced from the Unusual
 * Whales stock-state (which is available when the live WS feed is dark). Only
 * ever applied to an underlying quote box — never to an option/spread quote,
 * since these are the stock's own OHLV.
 */
export type QuoteFallback = {
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  prevClose: number | null;
};

function roundQuoteValue(n: number): number {
  return Math.round(n * 100) / 100;
}

export function getQuoteMetrics(priceData?: Pick<PriceData, "bid" | "ask"> | null): {
  bid: number | null;
  mid: number | null;
  ask: number | null;
  spread: number | null;
  spreadBps: number | null;
} {
  const bid = priceData?.bid ?? null;
  const ask = priceData?.ask ?? null;
  const mid = bid != null && ask != null ? roundQuoteValue((bid + ask) / 2) : null;
  const spread = bid != null && ask != null ? roundQuoteValue(ask - bid) : null;
  const midMagnitude = mid != null ? Math.abs(mid) : null;
  const spreadBps = spread != null && midMagnitude != null && midMagnitude > 0
    ? Math.round((spread / midMagnitude) * 10_000)
    : null;

  return { bid, mid, ask, spread, spreadBps };
}

export function formatSpreadTelemetry(
  priceData?: Pick<PriceData, "bid" | "ask"> | null,
): string {
  const { spread, mid } = getQuoteMetrics(priceData);
  if (spread == null) return "---";
  const midMagnitude = mid != null ? Math.abs(mid) : null;
  if (midMagnitude == null || midMagnitude <= 0) return fmtPrice(spread);
  return `${fmtPrice(spread)} / ${((spread / midMagnitude) * 100).toFixed(2)}%`;
}

export type ComboNetQuote = {
  symbol: string;
  bid: number | null;
  ask: number | null;
  last?: number | null;
};

/**
 * A BAG/combo is not a quoted instrument: every combo surface holds only a
 * signed per-leg sum (`computeNetOptionQuote`, `ComboOrderForm.netPrices`,
 * `useTargetQuote`). Wrap that net quote in a PriceData so a combo ticket feeds
 * the SAME `buildQuoteTelemetryModel` every single-leg surface uses instead of
 * hand-rolling a second model. Session OHLV stays null because no exchange
 * publishes a combo's high/low/volume — those fields render "---" honestly.
 */
export function comboQuotePriceData({ symbol, bid, ask, last }: ComboNetQuote): PriceData {
  const { mid } = getQuoteMetrics({ bid, ask });
  return {
    symbol,
    last: last ?? mid,
    lastIsCalculated: true,
    bid,
    ask,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: new Date().toISOString(),
  };
}

function formatMetricValue(value: number | null): string {
  return value != null ? fmtPrice(value) : "---";
}

function formatVolume(value: number | null): string {
  return value != null ? value.toLocaleString("en-US") : "---";
}

function lastFieldLabel(priceData: PriceData): string {
  return priceData.lastIsCalculated ? "MARK" : "LAST";
}

function dayChangeField(change: number | null): QuoteTelemetryField {
  return {
    label: "DAY",
    value: change != null ? `${change >= 0 ? "+" : ""}${change.toFixed(2)}%` : "---",
    tone: change == null ? null : change >= 0 ? "positive" : "negative",
    trend: change == null ? null : change > 0 ? "up" : change < 0 ? "down" : null,
  };
}

function pctChange(value: number | null, base: number | null): number | null {
  return value != null && value > 0 && base != null && base > 0
    ? ((value - base) / base) * 100
    : null;
}

/**
 * Market-closed model built purely from the UW stock-state fallback. There is
 * no live book after hours, so BID/MID/ASK/SPREAD stay "---"; LAST is the prior
 * session close (labelled CLOSE so it never masquerades as a live trade), and
 * HIGH/LOW/VOLUME/DAY come from the last completed session.
 */
function closedMarketModel(fallback: QuoteFallback): QuoteTelemetryModel {
  return {
    bid: { label: "BID", value: "---", tone: null, trend: null },
    mid: { label: "MID", value: "---", tone: null, trend: null },
    ask: { label: "ASK", value: "---", tone: null, trend: null },
    spread: { label: "SPREAD", value: "---", tone: null, trend: null },
    last: { label: "CLOSE", value: formatMetricValue(fallback.close), tone: null, trend: null },
    volume: { label: "VOLUME", value: formatVolume(fallback.volume), tone: null, trend: null },
    high: { label: "HIGH", value: formatMetricValue(fallback.high), tone: null, trend: null },
    low: { label: "LOW", value: formatMetricValue(fallback.low), tone: null, trend: null },
    day: dayChangeField(pctChange(fallback.close, fallback.prevClose)),
  };
}

const LIVE_QUOTE_MAX_AGE_MS = 5 * 60 * 1000;

/** A live quote needs a recent timestamp and at least one of last/bid/ask. The relay sometimes emits a
 * hollow tick (object present, every quote field null, volume 0) when the market
 * is closed — that must be treated as "no live quote", not as live data. */
function hasLiveQuote(priceData: PriceData, nowMs: number): boolean {
  const quoteMs = Date.parse(priceData.timestamp);
  if (!Number.isFinite(quoteMs) || quoteMs > nowMs + 60_000 || nowMs - quoteMs > LIVE_QUOTE_MAX_AGE_MS) {
    return false;
  }
  return priceData.last != null || priceData.bid != null || priceData.ask != null;
}

export function buildQuoteTelemetryModel(
  priceData: PriceData | null,
  fallback: QuoteFallback | null = null,
  nowMs = Date.now(),
): QuoteTelemetryModel | null {
  if (!priceData) {
    return fallback ? closedMarketModel(fallback) : null;
  }

  // Hollow tick + a fallback → render the prior session, enriched by any
  // non-null session fields the relay did manage to send.
  if (!hasLiveQuote(priceData, nowMs) && fallback) {
    return closedMarketModel({
      open: priceData.open ?? fallback.open,
      high: priceData.high ?? fallback.high,
      low: priceData.low ?? fallback.low,
      close: fallback.close,
      volume: priceData.volume != null && priceData.volume > 0 ? priceData.volume : fallback.volume,
      prevClose: fallback.prevClose,
    });
  }

  if (!hasLiveQuote(priceData, nowMs)) {
    return closedMarketModel({
      open: priceData.open,
      high: priceData.high,
      low: priceData.low,
      close: priceData.last ?? priceData.close,
      volume: priceData.volume,
      prevClose: priceData.close,
    });
  }

  const { bid, mid, ask } = getQuoteMetrics(priceData);
  const { last } = priceData;
  // Backfill session OHLV/volume from the stock-state fallback when the live
  // stream hasn't delivered them (common right after open or for thin names).
  const volume = priceData.volume ?? fallback?.volume ?? null;
  const high = priceData.high ?? fallback?.high ?? null;
  const low = priceData.low ?? fallback?.low ?? null;
  const close = priceData.close ?? fallback?.prevClose ?? null;
  const dayChange = pctChange(last, close);
  const spreadLabel = formatSpreadTelemetry(priceData);

  return {
    bid: { label: "BID", value: formatMetricValue(bid), tone: null, trend: null },
    mid: { label: "MID", value: formatMetricValue(mid), tone: null, trend: null },
    ask: { label: "ASK", value: formatMetricValue(ask), tone: null, trend: null },
    spread: { label: "SPREAD", value: spreadLabel, tone: null, trend: null },
    last: { label: lastFieldLabel(priceData), value: formatMetricValue(last), tone: null, trend: null },
    volume: { label: "VOLUME", value: formatVolume(volume), tone: null, trend: null },
    high: { label: "HIGH", value: formatMetricValue(high), tone: null, trend: null },
    low: { label: "LOW", value: formatMetricValue(low), tone: null, trend: null },
    day: dayChangeField(dayChange),
  };
}
