"use client";

import { useState, useEffect } from "react";

/**
 * Market state enum representing different trading time periods in ET.
 * - OPEN: Regular trading hours (9:30 AM - 4:00 PM ET)
 * - EXTENDED: Extended hours (4:00 AM - 9:30 AM OR 4:00 PM - 8:00 PM ET)
 * - CLOSED: Market closed (weekends or 8:00 PM - 4:00 AM ET)
 *
 * Note: This does NOT account for US market holidays. Holidays are rare (~10/year)
 * and not handling them does not introduce resource waste at the target scale.
 */
export enum MarketState {
  OPEN = "open",
  EXTENDED = "extended",
  CLOSED = "closed",
}

/**
 * Hook that returns the current market state based on Eastern Time.
 * Updates every minute (sufficient for market hour boundaries).
 *
 * @returns Current MarketState (OPEN, EXTENDED, or CLOSED)
 */
function marketStateAt(now = new Date()): MarketState {
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = et.getDay(); // 0=Sun, 6=Sat

  if (day === 0 || day === 6) return MarketState.CLOSED;

  const minutes = et.getHours() * 60 + et.getMinutes();

  if (minutes >= 9 * 60 + 30 && minutes <= 16 * 60) return MarketState.OPEN;
  if (
    (minutes >= 4 * 60 && minutes < 9 * 60 + 30)
    || (minutes > 16 * 60 && minutes <= 20 * 60)
  ) {
    return MarketState.EXTENDED;
  }
  return MarketState.CLOSED;
}

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