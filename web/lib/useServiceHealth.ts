"use client";

import { useEffect, useState } from "react";
import type { ServiceCategory } from "@/lib/serviceHealthWindows";

export type ServiceHealthRow = {
  service: string;
  state: string;
  /**
   * Trigger category. ``scheduled`` writers fire automatically;
   * ``on-demand`` writers only run when a user visits a page. The
   * banner uses this to surface dormant on-demand services
   * separately from degraded scheduled services.
   */
  category?: ServiceCategory;
  last_attempt_started_at: string | null;
  last_attempt_finished_at: string | null;
  /**
   * Raw JSON payload from ``service_health.last_error``. Diagnostic only
   * — UIs should render ``error_summary`` to stay clear of leaked JSON
   * structure.
   */
  last_error: string | null;
  /**
   * Pre-normalized single-line summary, populated by the route handler
   * via ``formatServiceHealthError``. ``null`` only when ``last_error``
   * itself is ``null``.
   */
  error_summary?: string | null;
  updated_at: string;
};

export type ServiceHealthResponse = {
  services: ServiceHealthRow[];
  failing: ServiceHealthRow[];
  /**
   * Count of rows that should fire the red degraded banner: ``error``
   * rows from any category plus ``stale`` rows from scheduled writers.
   */
  degraded_count?: number;
  /**
   * Count of on-demand rows past their freshness window — informational
   * only, surfaces in the banner as a soft chip rather than a red
   * treatment.
   */
  dormant_count?: number;
  summary: { total: number; failing_count: number };
  warning?: string;
};

const POLL_MS = 60_000;

export function useServiceHealth(): {
  data: ServiceHealthResponse | null;
  loading: boolean;
  error: string | null;
} {
  const [data, setData] = useState<ServiceHealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let inFlight = false;
    const fetchOnce = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const res = await fetch("/api/service-health", {
          method: "GET",
          cache: "no-store",
          signal: AbortSignal.timeout(10_000),
        });
        if (!res.ok) throw new Error(`Service health unavailable (${res.status})`);
        const json = (await res.json()) as ServiceHealthResponse;
        if (!cancelled) {
          setData(json);
          setError(null);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Service health unavailable");
          setLoading(false);
        }
      } finally {
        inFlight = false;
      }
    };

    void fetchOnce();
    const id = window.setInterval(fetchOnce, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  return { data, loading, error };
}
