"use client";

import { useEffect, useMemo, useState } from "react";
import type { PriceData } from "@/lib/pricesProtocol";

type IndexQuoteResponse = {
  price?: PriceData | null;
};

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
    if (!key) return;

    let cancelled = false;
    const pendingSymbols = key.split(",");
    void Promise.all(
      pendingSymbols.map(async (symbol) => {
        try {
          const res = await fetch(`/api/index-quote?symbol=${encodeURIComponent(symbol)}`, {
            cache: "no-store",
          });
          if (!res.ok) return null;
          const json = await res.json() as IndexQuoteResponse;
          return json.price && hasUsableIndexPrice(json.price) ? [symbol, json.price] as const : null;
        } catch {
          return null;
        }
      }),
    ).then((entries) => {
      if (cancelled) return;
      setFallbackPrices((prev) => {
        const next = { ...prev };
        let changed = false;
        for (const entry of entries) {
          if (!entry) continue;
          const [symbol, price] = entry;
          next[symbol] = price;
          changed = true;
        }
        return changed ? next : prev;
      });
    });

    return () => {
      cancelled = true;
    };
  }, [key]);

  return fallbackPrices;
}
