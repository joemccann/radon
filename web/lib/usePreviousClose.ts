"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { isIndexSymbol } from "./indexSymbols";
import { mostRecentSessionDate } from "./marketSession";
import type { PriceData } from "./pricesProtocol";

/**
 * Detects stock symbols with null `close` in WS prices and backfills
 * previous close from IB / UW / Yahoo via /api/previous-close.
 *
 * Returns a new prices record with `close` patched in for affected symbols.
 */
export function usePreviousClose(
  prices: Record<string, PriceData>,
): Record<string, PriceData> {
  const [closePrices, setClosePrices] = useState<Record<string, number>>({});
  const [retryVersion, setRetryVersion] = useState(0);
  const session = mostRecentSessionDate();
  const fetchedRef = useRef<{ session: string; symbols: Set<string> }>({
    session,
    symbols: new Set(),
  });
  if (fetchedRef.current.session !== session) {
    fetchedRef.current = { session, symbols: new Set() };
  }

  useEffect(() => {
    setClosePrices({});
  }, [session]);

  // Stock symbols (no underscores) with valid last but missing close
  const missingClose = useMemo(() => {
    return Object.keys(prices).filter((key) =>
      shouldBackfillPreviousClose(key, prices[key]) && !fetchedRef.current.symbols.has(key),
    );
  }, [prices, retryVersion]);

  // Stable key so the effect only fires when the missing list actually changes
  const missingKey = missingClose.join(",");

  useEffect(() => {
    if (!missingKey) return;
    const symbols = missingKey.split(",");

    // Mark in-flight to prevent duplicate requests
    for (const sym of symbols) fetchedRef.current.symbols.add(sym);

    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    const scheduleRetry = () => {
      if (retryTimer) return;
      retryTimer = setTimeout(() => setRetryVersion((value) => value + 1), 1_000);
    };

    fetch("/api/previous-close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols }),
    })
      .then((r) => {
        if (!r.ok) throw new Error(`Previous-close request failed (${r.status})`);
        return r.json();
      })
      .then((data: { closes: Record<string, number> }) => {
        const valid: Record<string, number> = {};
        for (const sym of symbols) {
          const value = data.closes?.[sym];
          if (typeof value === "number" && Number.isFinite(value) && value > 0) valid[sym] = value;
          else {
            fetchedRef.current.symbols.delete(sym);
            scheduleRetry();
          }
        }
        if (Object.keys(valid).length > 0) setClosePrices((prev) => ({ ...prev, ...valid }));
      })
      .catch(() => {
        // Allow retry on next render cycle
        for (const sym of symbols) fetchedRef.current.symbols.delete(sym);
        scheduleRetry();
      });
    return () => {
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [missingKey, retryVersion, session]);

  // Merge backfilled close values into prices
  return useMemo(() => {
    if (Object.keys(closePrices).length === 0) return prices;
    const merged: Record<string, PriceData> = {};
    for (const [key, pd] of Object.entries(prices)) {
      if ((pd.close == null || pd.close === 0) && closePrices[key] != null) {
        merged[key] = { ...pd, close: closePrices[key] };
      } else {
        merged[key] = pd;
      }
    }
    return merged;
  }, [prices, closePrices]);
}

export function shouldBackfillPreviousClose(symbol: string, price: PriceData): boolean {
  return !symbol.includes("_") &&
    !isIndexSymbol(symbol) &&
    price.last != null &&
    price.last !== 0 &&
    (price.close == null || price.close === 0);
}
