"use client";

import { useEffect, useRef, useState } from "react";

/** Raw response shape from GET /api/short-availability/[ticker] */
export interface ShortAvailabilityData {
  ticker: string;
  /** true = easy-to-borrow; false = not shortable; null = unknown */
  shortable: boolean | null;
  /** IB tick 46 raw difficulty score */
  difficulty: number | null;
  /** IB tick 89 — available shares to short */
  shortable_shares: number | null;
  /** Annualized fee rate % (UW fallback) */
  fee_rate: number | null;
  /** Annualized rebate rate % (UW) */
  rebate_rate: number | null;
  /** Data source */
  source: "ib" | "uw" | "none";
  /** ISO 8601 timestamp of the reading */
  as_of: string;
  /** true when IB and UW both returned no data */
  missing: boolean;
}

export type ShortAvailabilityStatus =
  | "no-locate"   // missing or not shortable
  | "htb"         // hard to borrow — locate only
  | "easy";       // easy to borrow

function deriveStatus(data: ShortAvailabilityData): ShortAvailabilityStatus {
  if (data.missing || data.shortable === false) return "no-locate";
  if (data.shortable === true) return "easy";
  return "htb";
}

export interface UseShortAvailabilityResult {
  status: ShortAvailabilityStatus | null;
  data: ShortAvailabilityData | null;
  loading: boolean;
  error: string | null;
}

/**
 * Fetches short-availability data for a single ticker from the Next.js proxy
 * route, which in turn calls FastAPI GET /short-availability/{ticker}.
 *
 * Only fires when `enabled` is true so callers can gate on
 * action=SELL/SHORT + no held position. Returns null status while loading.
 *
 * Cache: no-store per the disk-backed route contract.
 */
export function useShortAvailability(
  ticker: string | null,
  enabled: boolean,
): UseShortAvailabilityResult {
  const [data, setData] = useState<ShortAvailabilityData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const lastTickerRef = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled || !ticker) {
      setData(null);
      setLoading(false);
      setError(null);
      return;
    }

    if (lastTickerRef.current === ticker && data != null) return;

    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    lastTickerRef.current = ticker;

    setLoading(true);
    setError(null);

    fetch(`/api/short-availability/${encodeURIComponent(ticker)}`, {
      cache: "no-store",
      signal: ctrl.signal,
    })
      .then((r) => r.json() as Promise<ShortAvailabilityData>)
      .then((json) => {
        setData(json);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Short availability unavailable");
        setLoading(false);
      });

    return () => {
      ctrl.abort();
    };
  }, [ticker, enabled]); // eslint-disable-line react-hooks/exhaustive-deps

  const status = data ? deriveStatus(data) : null;

  return { status, data, loading, error };
}
