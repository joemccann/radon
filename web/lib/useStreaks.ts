"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { StreaksData } from "./streaks";

/**
 * On-demand per-symbol fetch of GET /api/streaks. No polling: the series is
 * daily, so data changes once per session; the panel refetches on symbol
 * change or explicit refresh. Stale responses from an abandoned symbol are
 * dropped via a request sequence guard.
 */

/** Above the route's own 60s upstream budget so its structured error wins. */
const STREAKS_REQUEST_TIMEOUT_MS = 65_000;

export type UseStreaksReturn = {
  data: StreaksData | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
};

export function useStreaks(symbol: string): UseStreaksReturn {
  const [data, setData] = useState<StreaksData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const seqRef = useRef(0);

  useEffect(() => {
    if (!symbol) return;
    const seq = ++seqRef.current;
    let cancelled = false;

    const run = async () => {
      setLoading(true);
      try {
        const res = await fetch(`/api/streaks?symbol=${encodeURIComponent(symbol)}`, {
          method: "GET",
          cache: "no-store",
          signal: AbortSignal.timeout(STREAKS_REQUEST_TIMEOUT_MS),
        });
        if (cancelled || seq !== seqRef.current) return;
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as { error?: string };
          throw new Error(body.error ?? `Streaks fetch failed (${res.status})`);
        }
        const json = (await res.json()) as StreaksData;
        if (cancelled || seq !== seqRef.current) return;
        setData(json);
        setError(null);
      } catch (err) {
        if (cancelled || seq !== seqRef.current) return;
        setData(null);
        setError(err instanceof Error ? err.message : "Streaks fetch failed");
      } finally {
        if (!cancelled && seq === seqRef.current) setLoading(false);
      }
    };

    void run();
    return () => {
      cancelled = true;
    };
  }, [symbol, nonce]);

  const refresh = useCallback(() => {
    setNonce((n) => n + 1);
  }, []);

  return { data, loading, error, refresh };
}
