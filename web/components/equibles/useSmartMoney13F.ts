"use client";

import { useEffect, useState } from "react";
import type { SmartMoney13FData } from "./smartMoney13F";

/**
 * Per-ticker 13F depth read.
 *
 * GET-only and fetched once per ticker: 13F is quarterly, so polling would
 * bill requests against a source that cannot move between renders. The route
 * always answers 200, so a `missing: true` body is a settled empty state, not
 * an error.
 */
export type UseSmartMoney13FReturn = {
  data: SmartMoney13FData | null;
  loading: boolean;
  error: string | null;
};

export function useSmartMoney13F(ticker: string | null | undefined): UseSmartMoney13FReturn {
  const [data, setData] = useState<SmartMoney13FData | null>(null);
  const [loading, setLoading] = useState(Boolean(ticker));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ticker) {
      setData(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    fetch(`/api/equibles-smart-money-13f?ticker=${encodeURIComponent(ticker)}`, {
      cache: "no-store",
    })
      .then((res) => res.json())
      .then((payload: SmartMoney13FData) => {
        if (cancelled) return;
        setData(payload);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load 13F positioning");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [ticker]);

  return { data, loading, error };
}
