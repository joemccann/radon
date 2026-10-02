"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { isIndexSymbol } from "./indexSymbols";
import { mostRecentSessionDate } from "./marketSession";
import type { PriceData } from "./pricesProtocol";

const RETRY_BASE_MS = 1_000;
const RETRY_MAX_MS = 60_000;
/** A symbol no source can serve stops being asked for after this many misses per session. */
const MAX_ATTEMPTS = 5;

class RateLimited extends Error {
  constructor(readonly retryAfterMs: number) {
    super("Previous-close request rate limited");
  }
}

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
  const retryTimers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const generation = useRef(0);
  const session = mostRecentSessionDate();
  const fetchedRef = useRef<{ session: string; symbols: Set<string>; attempts: Map<string, number> }>({
    session,
    symbols: new Set(),
    attempts: new Map(),
  });
  if (fetchedRef.current.session !== session) {
    fetchedRef.current = { session, symbols: new Set(), attempts: new Map() };
  }

  useEffect(() => {
    setClosePrices({});
    const timers = retryTimers.current;
    return () => {
      generation.current += 1;
      for (const timer of timers) clearTimeout(timer);
      timers.clear();
    };
  }, [session]);

  // Stock symbols (no underscores) with valid last but missing close
  const missingClose = useMemo(() => {
    return Object.keys(prices).filter((key) =>
      shouldBackfillPreviousClose(key, prices[key]) && !fetchedRef.current.symbols.has(key),
    );
  }, [prices, retryVersion, session]);

  // Stable key so the effect only fires when the missing list actually changes
  const missingKey = missingClose.join(",");

  useEffect(() => {
    if (!missingKey) return;
    const symbols = missingKey.split(",");
    const requestState = fetchedRef.current;
    const requestGeneration = generation.current;
    const isCurrentRequest = () => requestState === fetchedRef.current && requestGeneration === generation.current;

    // Mark in-flight to prevent duplicate requests
    for (const sym of symbols) requestState.symbols.add(sym);

    const scheduleRetry = (failed: string[], delayMs: number) => {
      // Hold the in-flight marks throughout the cooldown. Streaming price
      // renders must not make these symbols eligible before the timer fires.
      const timer = setTimeout(() => {
        retryTimers.current.delete(timer);
        if (!isCurrentRequest()) return;
        for (const sym of failed) requestState.symbols.delete(sym);
        setRetryVersion((value) => value + 1);
      }, delayMs);
      retryTimers.current.add(timer);
    };
    // Exponential backoff per symbol; once MAX_ATTEMPTS misses land the symbol
    // stays marked fetched, so it is not asked for again this session.
    const releaseForRetry = (failed: string[]) => {
      let delayMs = 0;
      const retrySymbols: string[] = [];
      for (const sym of failed) {
        const attempts = (requestState.attempts.get(sym) ?? 0) + 1;
        requestState.attempts.set(sym, attempts);
        if (attempts >= MAX_ATTEMPTS) continue;
        retrySymbols.push(sym);
        delayMs = Math.max(delayMs, Math.min(RETRY_BASE_MS * 2 ** (attempts - 1), RETRY_MAX_MS));
      }
      if (delayMs > 0) scheduleRetry(retrySymbols, delayMs);
    };

    fetch("/api/previous-close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols }),
    })
      .then((r) => {
        if (r.status === 429) {
          const seconds = Number(r.headers.get("Retry-After"));
          throw new RateLimited(Number.isFinite(seconds) && seconds > 0 ? seconds * 1_000 : RETRY_MAX_MS);
        }
        if (!r.ok) throw new Error(`Previous-close request failed (${r.status})`);
        return r.json();
      })
      .then((data: { closes: Record<string, number> }) => {
        if (!isCurrentRequest()) return;
        const valid: Record<string, number> = {};
        const failed: string[] = [];
        for (const sym of symbols) {
          const value = data.closes?.[sym];
          if (typeof value === "number" && Number.isFinite(value) && value > 0) valid[sym] = value;
          else failed.push(sym);
        }
        releaseForRetry(failed);
        if (Object.keys(valid).length > 0) setClosePrices((prev) => ({ ...prev, ...valid }));
      })
      .catch((error: unknown) => {
        if (!isCurrentRequest()) return;
        if (error instanceof RateLimited) {
          // A 429 says nothing about the symbols, so it does not count as a miss.
          scheduleRetry(symbols, error.retryAfterMs);
          return;
        }
        releaseForRetry(symbols);
      });
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
