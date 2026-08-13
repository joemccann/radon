"use client";

import { useEffect, useMemo, useState } from "react";
import type { PriceData } from "@/lib/pricesProtocol";

type IndexQuoteResponse = {
  price?: PriceData | null;
};

export const INDEX_FALLBACK_REFRESH_MS = 60_000;

export function hasUsableIndexPrice(price: PriceData | null | undefined): boolean {
  if (!price) return false;
  return [price.last, price.close, price.bid, price.ask].some(
    (value) => typeof value === "number" && Number.isFinite(value) && value > 0,
  );
}

export function mergeIndexFallbackPrices(
  livePrices: Record<string, PriceData>,
  fallbackPrices: Record<string, PriceData>,
): Record<string, PriceData> {
  if (Object.keys(fallbackPrices).length === 0) return livePrices;

  let changed = false;
  const merged = { ...livePrices };
  for (const [symbol, fallback] of Object.entries(fallbackPrices)) {
    if (!hasUsableIndexPrice(merged[symbol]) && hasUsableIndexPrice(fallback)) {
      merged[symbol] = fallback;
      changed = true;
    }
  }
  return changed ? merged : livePrices;
}

export function useIndexQuoteFallback(symbols: string[]): Record<string, PriceData> {
  const [fallbackPrices, setFallbackPrices] = useState<Record<string, PriceData>>({});
  const key = useMemo(
    () => [...new Set(symbols.map((s) => s.trim().toUpperCase()).filter(Boolean))].sort().join(","),
    [symbols],
  );

  useEffect(() => {
    if (!key) {
      setFallbackPrices({});
      return;
    }

    let cancelled = false;
    const pendingSymbols = key.split(",");
    const refresh = async () => {
      const entries = await Promise.all(pendingSymbols.map(async (symbol) => {
        try {
          const res = await fetch(`/api/index-quote?symbol=${encodeURIComponent(symbol)}`, {
            cache: "no-store",
          });
          if (!res.ok) return [symbol, null] as const;
          const json = await res.json() as IndexQuoteResponse;
          return [
            symbol,
            json.price && hasUsableIndexPrice(json.price) ? json.price : null,
          ] as const;
        } catch {
          return [symbol, null] as const;
        }
      }));
      if (cancelled) return;
      setFallbackPrices(() => {
        const next: Record<string, PriceData> = {};
        for (const [symbol, price] of entries) {
          if (price) next[symbol] = price;
        }
        return next;
      });
    };
    void refresh();
    const timer = setInterval(() => void refresh(), INDEX_FALLBACK_REFRESH_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [key]);

  return fallbackPrices;
}
