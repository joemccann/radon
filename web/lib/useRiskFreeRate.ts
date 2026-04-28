"use client";

import { useEffect, useState } from "react";

/**
 * Latest effective Fed Funds rate (FRED:DFF) as a decimal — used as `r`
 * in Black-Scholes implied-value calculations. Returns 0 until the fetch
 * resolves, so consumers always have a usable number.
 *
 * Single fetch on mount; relies on the route's 24h revalidation for freshness.
 */
export function useRiskFreeRate(): number {
  const [rate, setRate] = useState<number>(0);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/risk-free-rate")
      .then((r) => (r.ok ? r.json() : null))
      .then((data: { rate?: number } | null) => {
        if (cancelled || !data || typeof data.rate !== "number" || !Number.isFinite(data.rate)) return;
        setRate(data.rate);
      })
      .catch(() => {
        // silent — falls back to 0
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return rate;
}
