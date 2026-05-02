"use client";

import { useEffect, useState } from "react";
import type { CtaCacheMeta } from "@/lib/ctaFreshness";

export type CtaRow = {
  underlying: string;
  position_today: number;
  position_yesterday: number;
  position_1m_ago: number;
  percentile_1m: number;
  percentile_3m: number;
  percentile_1y: number;
  z_score_3m: number;
};

export type CtaCache = {
  date: string | null;
  fetched_at: string | null;
  source?: string | null;
  tables: {
    main: CtaRow[];
    index: CtaRow[];
    commodity: CtaRow[];
    currency: CtaRow[];
  } | null;
  cache_meta: CtaCacheMeta;
  sync_health?: {
    service: string;
    state: string;
    target_date: string | null;
    latest_available_date?: string | null;
    last_attempt_started_at?: string | null;
    last_attempt_finished_at?: string | null;
    last_successful_date?: string | null;
    last_successful_at?: string | null;
    last_cache_path?: string | null;
    attempt_count?: number | null;
    last_error?: { type: string; message: string } | null;
    last_run_source?: string | null;
    artifacts?: Record<string, string>;
    message?: string | null;
  } | null;
  sync_status?: {
    service: string;
    state?: string;
    status?: string;
    trigger?: string | null;
    target_date: string | null;
    started_at?: string | null;
    finished_at?: string | null;
    duration_ms?: number | null;
    attempt_count?: number | null;
    cache_path?: string | null;
    error_type?: string | null;
    error_excerpt?: string | null;
    artifact_log_path?: string | null;
    last_error?: { type: string; message: string } | null;
    message?: string | null;
  } | null;
};

export function useMenthorqCta(): { data: CtaCache | null; loading: boolean; error: string | null } {
  const [data, setData] = useState<CtaCache | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    fetch("/api/menthorq/cta", { cache: "no-store" })
      .then(async (res) => {
        const json = (await res.json()) as CtaCache;
        if (!active) return;
        setData(json);
        if (!res.ok) {
          const syncError = json.sync_health?.last_error?.message
            ?? json.sync_status?.last_error?.message
            ?? json.sync_status?.error_excerpt;
          setError(
            syncError ?? `CTA data unavailable (${res.status})`,
          );
        }
        setLoading(false);
      })
      .catch(() => {
        if (!active) return;
        setError("Failed to load CTA positioning data");
        setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  return { data, loading, error };
}
