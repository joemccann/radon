"use client";

import { useEffect, useRef, useState } from "react";
import type { LivelinePoint } from "liveline";
import type { PriceData } from "@/lib/pricesProtocol";
import { generateMockHistory, getBasePrice, nextMockPrice } from "./mockPriceGenerator";

/**
 * Resolve the best available price for chart rendering.
 *
 * Priority:
 *   1. `last` — actual last-trade price (positive)
 *   2. mid = (bid + ask) / 2 — when last is absent but both sides are quoted
 *   3. null — no usable price
 *
 * Returns `{ price, isMid, isCalculated }` so callers can surface a visual
 * indicator when falling back to mid, and freeze the chart (no random-walk
 * mock ticks) when the value is IB's model mark rather than a live trade.
 */
export function resolveChartPrice(
  pd: PriceData | undefined,
  valueKind: "price" | "spread-net" = "price",
): { price: number | null; isMid: boolean; isCalculated: boolean } {
  if (!pd) return { price: null, isMid: false, isCalculated: false };

  // Last-trade price takes full priority
  if (pd.last != null && Number.isFinite(pd.last) && (valueKind === "spread-net" || pd.last > 0)) {
    return { price: pd.last, isMid: false, isCalculated: pd.lastIsCalculated === true };
  }

  // Mid fallback — requires both sides of the quote
  if (pd.bid != null && pd.ask != null && Number.isFinite(pd.bid) && Number.isFinite(pd.ask)) {
    const mid = (pd.bid + pd.ask) / 2;
    if (valueKind === "spread-net" || mid > 0) {
      return { price: mid, isMid: true, isCalculated: false };
    }
  }

  return { price: null, isMid: false, isCalculated: false };
}

interface PriceHistoryResult {
  data: LivelinePoint[];
  value: number | null;
  loading: boolean;
  /** True when chart values are derived from mid price (no last-trade available). */
  isMid: boolean;
  /** True when the displayed value is IB's calculated mark, not a live trade. */
  isCalculated: boolean;
}

/**
 * Accumulates a LivelinePoint[] from real-time price updates.
 *
 * - When a real WS tick is available (last or mid), seed a flat history at
 *   that value and append real updates as they arrive.
 * - When the value is IB's calculated mark (`lastIsCalculated=true`,
 *   typical for illiquid options that only return ASK), seed a flat
 *   history at that mark and DO NOT mock-walk — the value rarely changes
 *   so animating it would lie to the user (the ~$99 random-walk-from-100
 *   bug fixed 2026-05-20).
 * - When no real or calculated price exists yet, leave the chart empty
 *   (loading=true) rather than fabricate one.
 */
const MOCK_WALK_FALLBACK = false; // intentionally off — mock walk masks real outages.

export function usePriceHistory(
  ticker: string | null,
  prices: Record<string, PriceData>,
  maxPoints = 200,
  valueKind: "price" | "spread-net" = "price",
): PriceHistoryResult {
  const [data, setData] = useState<LivelinePoint[]>([]);
  const [value, setValue] = useState<number | null>(null);
  const [isMid, setIsMid] = useState(false);
  const [isCalculated, setIsCalculated] = useState(false);
  const lastRealRef = useRef(0);
  const mockTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastPriceRef = useRef(0);
  const tickerRef = useRef(ticker);

  // Reset on ticker change
  useEffect(() => {
    tickerRef.current = ticker;
    if (!ticker) {
      setData([]);
      setValue(null);
      setIsMid(false);
      setIsCalculated(false);
      return;
    }

    const { price: resolvedBase, isMid: baseMid, isCalculated: baseCalc } = resolveChartPrice(prices[ticker], valueKind);

    if (resolvedBase == null) {
      // No real price — start empty. The mock-walk path is disabled to
      // avoid surfacing fictitious traces (e.g. illiquid option keys
      // landed on the default $100 base and walked from there).
      setData([]);
      setValue(null);
      setIsMid(false);
      setIsCalculated(false);
      lastPriceRef.current = 0;
      lastRealRef.current = 0;
      return;
    }

    // Seed with a flat line at the resolved value rather than a random
    // walk. We have a real anchor; jittering around it is misleading.
    const now = Date.now() / 1000;
    const seed: LivelinePoint[] = [];
    for (let i = 59; i >= 0; i--) {
      seed.push({ time: now - i, value: resolvedBase });
    }
    setData(seed);
    setValue(resolvedBase);
    setIsMid(baseMid);
    setIsCalculated(baseCalc);
    lastPriceRef.current = resolvedBase;
    lastRealRef.current = baseCalc ? 0 : now; // calculated marks don't count as "real" updates

    return () => {
      if (mockTimerRef.current) clearTimeout(mockTimerRef.current);
    };
  }, [ticker]); // eslint-disable-line react-hooks/exhaustive-deps

  // Append real price updates (last-trade or mid fallback)
  useEffect(() => {
    if (!ticker) return;
    const pd = prices[ticker];
    const { price: resolved, isMid: mid, isCalculated: calc } = resolveChartPrice(pd, valueKind);
    if (resolved == null) return;
    if (resolved === lastPriceRef.current) return; // no-op append

    const now = Date.now() / 1000;
    if (!calc) lastRealRef.current = now;
    lastPriceRef.current = resolved;

    setData((prev) => {
      const next = [...prev, { time: now, value: resolved }];
      return next.length > maxPoints ? next.slice(next.length - maxPoints) : next;
    });
    setValue(resolved);
    setIsMid(mid);
    setIsCalculated(calc);
  }, [ticker, prices[ticker ?? ""]?.last, prices[ticker ?? ""]?.bid, prices[ticker ?? ""]?.ask, prices[ticker ?? ""]?.lastIsCalculated, maxPoints, valueKind]); // eslint-disable-line react-hooks/exhaustive-deps

  // Optional mock-walk fallback. Disabled by default — a recurring class
  // of bugs comes from chart consumers misreading mock data as real
  // (most recently the USAX option page showing $99.41 derived from the
  // default $100 base price for an unknown option key).
  useEffect(() => {
    if (!MOCK_WALK_FALLBACK || !ticker) return;

    const tick = () => {
      if (tickerRef.current !== ticker) return;
      const now = Date.now() / 1000;
      const sinceReal = now - lastRealRef.current;
      if (sinceReal > 3 || lastRealRef.current === 0) {
        const newPrice = nextMockPrice(lastPriceRef.current);
        lastPriceRef.current = newPrice;
        setData((prev) => {
          const next = [...prev, { time: now, value: newPrice }];
          return next.length > maxPoints ? next.slice(next.length - maxPoints) : next;
        });
        setValue(newPrice);
      }
      mockTimerRef.current = setTimeout(tick, 1000);
    };
    mockTimerRef.current = setTimeout(tick, 1000);
    return () => {
      if (mockTimerRef.current) clearTimeout(mockTimerRef.current);
    };
  }, [ticker, maxPoints]);

  return { data, value, loading: data.length === 0, isMid, isCalculated };
}

/** Simple string hash for deterministic seeding per ticker. */
function hashStr(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  }
  return h;
}
