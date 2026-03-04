"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { PriceData } from "./pricesProtocol";

/**
 * Detects stock symbols with null `close` in WS prices and backfills
 * previous close from Yahoo Finance / UW via /api/previous-close.
 *
 * Returns a new prices record with `close` patched in for affected symbols.
 */
export function usePreviousClose(
  prices: Record<string, PriceData>,
): Record<string, PriceData> {
  const [closePrices, setClosePrices] = useState<Record<string, number>>({});
  const fetchedRef = useRef<Set<string>>(new Set());

  // Stock symbols (no underscores) with valid last but missing close
  const missingClose = useMemo(() => {
    return Object.keys(prices).filter(
      (key) =>
        !key.includes("_") &&
        prices[key].last != null &&
        prices[key].last !== 0 &&
        (prices[key].close == null || prices[key].close === 0) &&
        !fetchedRef.current.has(key),
    );
  }, [prices]);

  // Stable key so the effect only fires when the missing list actually changes
  const missingKey = missingClose.join(",");

  useEffect(() => {
    if (!missingKey) return;
    const symbols = missingKey.split(",");

    // Mark in-flight to prevent duplicate requests
    for (const sym of symbols) fetchedRef.current.add(sym);

    fetch("/api/previous-close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols }),
    })
      .then((r) => r.json())
      .then((data: { closes: Record<string, number> }) => {
        if (data.closes && Object.keys(data.closes).length > 0) {
          setClosePrices((prev) => ({ ...prev, ...data.closes }));
        }
      })
      .catch(() => {
        // Allow retry on next render cycle
        for (const sym of symbols) fetchedRef.current.delete(sym);
      });
  }, [missingKey]);

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
