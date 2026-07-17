"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  isOptionsExposurePayload,
  type OptionsExposureFrequency,
  type OptionsExposurePayload,
} from "./optionsExposure";

export interface UseOptionsExposureResult {
  data: OptionsExposurePayload | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useOptionsExposure(
  symbol: string,
  frequency: OptionsExposureFrequency,
): UseOptionsExposureResult {
  const [data, setData] = useState<OptionsExposurePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const normalizedSymbol = symbol.trim().toUpperCase();

  const fetchExposure = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);

    try {
      const query = new URLSearchParams({ symbol: normalizedSymbol, frequency });
      const response = await fetch(`/api/options/exposure?${query.toString()}`, {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`Options exposure is temporarily unavailable (HTTP ${response.status})`);
      }

      const payload: unknown = await response.json();
      if (!isOptionsExposurePayload(payload)) {
        throw new Error("Options exposure returned an invalid measurement");
      }
      if (!controller.signal.aborted) {
        setData(payload);
        setError(null);
      }
    } catch (cause) {
      if (controller.signal.aborted) return;
      setData(null);
      setError(cause instanceof Error ? cause.message : "Options exposure is temporarily unavailable");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [frequency, normalizedSymbol]);

  useEffect(() => {
    setData(null);
    setError(null);
    void fetchExposure();
    return () => controllerRef.current?.abort();
  }, [fetchExposure]);

  return { data, loading, error, refresh: fetchExposure };
}
