"use client";

import { useEffect, useMemo, useState } from "react";
import type { PriceData } from "@/lib/pricesProtocol";
import { useGlobexOpen } from "@/lib/futuresSession";
import { hasUsableIndexPrice } from "@/lib/useIndexQuoteFallback";

/**
 * Yahoo delayed-quote fallback for the ES/NQ/RTY header strip, the futures twin
 * of `useIndexQuoteFallback`. Fetches `/api/futures-quote` only for the symbols
 * the relay isn't streaming and only while the CME Globex session is open (the
 * strip is hidden otherwise, and Yahoo would only return stale settlements).
 * Returns a sparse map; merge via `mergeIndexFallbackPrices` so a live relay
 * value always wins. Refreshes on an interval since delayed quotes drift.
 */
const REFRESH_MS = 30_000;

type FuturesQuoteResponse = {
  price?: PriceData | null;
};

export function useFuturesQuoteFallback(symbols: string[]): Record<string, PriceData> {
  const globexOpen = useGlobexOpen();
  const [fallbackPrices, setFallbackPrices] = useState<Record<string, PriceData>>({});
  const [tick, setTick] = useState(0);

  const key = useMemo(
    () => globexOpen
      ? [...new Set(symbols.map((s) => s.trim().toUpperCase()).filter(Boolean))].sort().join(",")
      : "",
    [symbols, globexOpen],
  );

  useEffect(() => {
    if (!key) return;
    const interval = setInterval(() => setTick((t) => t + 1), REFRESH_MS);
    return () => clearInterval(interval);
  }, [key]);

  useEffect(() => {
    if (!key) return;

    let cancelled = false;
    const pendingSymbols = key.split(",");
    void Promise.all(
      pendingSymbols.map(async (symbol) => {
        try {
          const res = await fetch(`/api/futures-quote?symbol=${encodeURIComponent(symbol)}`, {
            cache: "no-store",
          });
          if (!res.ok) return null;
          const json = await res.json() as FuturesQuoteResponse;
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
  }, [key, tick]);

  return fallbackPrices;
}
