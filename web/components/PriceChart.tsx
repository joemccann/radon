"use client";

import { useMemo } from "react";
import { Liveline } from "liveline";
import type { PriceData } from "@/lib/pricesProtocol";
import { usePriceHistory } from "@/lib/usePriceHistory";

interface PriceChartProps {
  ticker: string;
  prices: Record<string, PriceData>;
  /** Override the price key used for charting (e.g. option contract key instead of underlying) */
  priceKey?: string;
}

export default function PriceChart({ ticker, prices, priceKey }: PriceChartProps) {
  const chartKey = priceKey ?? ticker;
  const { data, value, loading } = usePriceHistory(chartKey, prices);

  const priceData = prices[chartKey];
  const closePrice = priceData?.close ?? null;

  const color = useMemo(() => {
    if (!closePrice || !value) return "#22c55e";
    return value >= closePrice ? "#22c55e" : "#ef4444";
  }, [value, closePrice]);

  const referenceLine = useMemo(() => {
    if (closePrice == null || closePrice <= 0) return undefined;
    return { value: closePrice, label: "PREV CLOSE" };
  }, [closePrice]);

  return (
    <div className="price-chart-container">
      <Liveline
        data={data}
        value={value}
        theme="dark"
        color={color}
        grid={true}
        badge={true}
        scrub={true}
        fill={true}
        formatValue={(v: number) => `$${v.toFixed(2)}`}
        referenceLine={referenceLine}
        loading={loading}
        padding={{ top: 16, right: 80, bottom: 28, left: 12 }}
      />
    </div>
  );
}
