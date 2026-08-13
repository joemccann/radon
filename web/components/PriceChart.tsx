"use client";

import { useMemo } from "react";
import { Liveline } from "liveline";
import { resolveChartSeriesColor } from "@/lib/chartSystem";
import type { PriceData } from "@/lib/pricesProtocol";
import { usePriceHistory } from "@/lib/usePriceHistory";
import ChartPanel from "./charts/ChartPanel";

interface PriceChartProps {
  ticker: string;
  prices: Record<string, PriceData>;
  /** Override the price key used for charting (e.g. option contract key instead of underlying) */
  priceKey?: string;
  /** Optional pre-resolved priceData that takes precedence over `prices[chartKey]`.
   *  Set by the ticker page when the WS stream lacks a `last` for an option
   *  position so the chart can plot IB's calculated mark instead of the
   *  default-base mock walk. */
  priceData?: PriceData | null;
  /** What the charted value represents.
   *  - `"price"` (default): a raw share/contract price level. Badge reads
   *    MIDPRICE/MARK, value formats as `$X.XX`, color tracks value vs PREV CLOSE.
   *  - `"spread-net"`: a multi-leg spread NET (signed: credit negative, debit
   *    positive per the Sign Convention). The net has no meaningful close
   *    baseline, so the badge reads NET CREDIT/NET DEBIT, the value formats as
   *    a credit/debit (sign preserved), and the pill stays brand-neutral
   *    instead of implying profit-green. */
  valueKind?: "price" | "spread-net";
  /** Theme forwarded from the shell — defaults to 'dark' to preserve existing behavior */
  theme?: "dark" | "light";
}

function formatSpreadNet(v: number): string {
  const magnitude = `$${Math.abs(v).toFixed(2)}`;
  if (v < 0) return `(${magnitude}) cr`;
  return `${magnitude} db`;
}

export default function PriceChart({
  ticker,
  prices,
  priceKey,
  priceData: priceDataOverride,
  valueKind = "price",
  theme = "dark",
}: PriceChartProps) {
  const isSpreadNet = valueKind === "spread-net";
  const chartKey = priceKey ?? ticker;
  // If the caller pre-resolved priceData (e.g. merged calculated mark in),
  // expose it under `chartKey` so usePriceHistory sees the same value the
  // panel does. Memoize to keep the hook's prices ref stable.
  const effectivePrices = useMemo(() => {
    if (!priceDataOverride) return prices;
    return { ...prices, [chartKey]: priceDataOverride };
  }, [prices, priceDataOverride, chartKey]);

  const { data, value, loading, isMid, isCalculated } = usePriceHistory(chartKey, effectivePrices, 200, valueKind);

  const priceData = priceDataOverride ?? prices[chartKey];
  const closePrice = priceData?.close ?? null;
  const positiveColor = useMemo(() => resolveChartSeriesColor("primary"), []);
  const negativeColor = useMemo(() => resolveChartSeriesColor("fault"), []);
  const neutralColor = useMemo(() => resolveChartSeriesColor("neutral"), []);

  // A spread net has no meaningful close baseline to compare against, and its
  // sign is a credit/debit convention — not profit/loss. Color it neutrally so
  // a negative net (a legitimate credit) doesn't read as a fault/loss.
  const color = useMemo(() => {
    if (isSpreadNet) return neutralColor;
    if (!closePrice || !value) return positiveColor;
    return value >= closePrice ? positiveColor : negativeColor;
  }, [isSpreadNet, value, closePrice, positiveColor, negativeColor, neutralColor]);

  const referenceLine = useMemo(() => {
    if (isSpreadNet) return undefined;
    if (closePrice == null || closePrice <= 0) return undefined;
    return { value: closePrice, label: "PREV CLOSE" };
  }, [isSpreadNet, closePrice]);

  const spreadNetBadgeLabel =
    value == null ? "NET UNAVAILABLE" : value < 0 ? "NET CREDIT" : "NET DEBIT";

  return (
    <ChartPanel
      family="live-trace"
      title={chartKey === ticker ? "Live Price Trace" : "Live Position Trace"}
      className="chart-panel-inline price-chart-panel"
      bodyClassName="price-chart-panel-body"
      contentClassName="price-chart-panel-content"
      dataTestId="price-chart-panel"
    >
      <div className="price-chart-container">
        {isSpreadNet ? (
          <div
            className="price-chart-mid-badge"
            aria-label="Chart value is the multi-leg spread net (credit negative, debit positive)"
          >
            {spreadNetBadgeLabel}
          </div>
        ) : (
          <>
            {isCalculated && (
              <div className="price-chart-mid-badge" aria-label="Chart value is IB's calculated mark (no live trade)">
                MARK
              </div>
            )}
            {!isCalculated && isMid && (
              <div className="price-chart-mid-badge" aria-label="Chart values are mid price (bid+ask)/2">
                MIDPRICE
              </div>
            )}
          </>
        )}
        <Liveline
          data={data}
          value={value ?? 0}
          theme={theme}
          color={color}
          grid={true}
          badge={true}
          scrub={true}
          fill={true}
          formatValue={(v: number) => (isSpreadNet ? formatSpreadNet(v) : `$${v.toFixed(2)}`)}
          referenceLine={referenceLine}
          loading={loading}
          padding={{ top: 16, right: 80, bottom: 28, left: 12 }}
        />
      </div>
    </ChartPanel>
  );
}
