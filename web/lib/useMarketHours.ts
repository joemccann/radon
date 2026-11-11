"use client";

import { useState, useEffect } from "react";
import { getMarketStateFromDate } from "@/lib/marketCalendar";

/**
 * Market state in ET:
 * - OPEN: regular session (09:30-16:00, or the half-day close)
 * - EXTENDED: 04:00-09:30 and close-20:00 on a trading day
 * - CLOSED: everything else, including weekends and exchange holidays
 */
export enum MarketState {
  OPEN = "open",
  EXTENDED = "extended",
  CLOSED = "closed",
}

export function marketStateAt(now = new Date()): MarketState {
  return getMarketStateFromDate(now) as MarketState;
}

/** Current MarketState, re-checked every minute. */
export function useMarketHours(): MarketState {
  const [state, setState] = useState<MarketState>(marketStateAt);

  useEffect(() => {
    const check = () => setState(marketStateAt());
    check();
    const interval = setInterval(check, 60_000);
    return () => clearInterval(interval);
  }, []);

  return state;
}
