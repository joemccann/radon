"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  isRvRatioMissingPayload,
  isRvRatioPayload,
  RV_RATIO_SCAN_TIMEOUT_MS,
  type RvRatioPayload,
} from "./rvRatio";

export interface UseRvRatioResult {
  data: RvRatioPayload | null;
  loading: boolean;
  /** True while the synchronous scan POST is in flight (missing/stale snapshot). */
  scanning: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

/**
 * Per-symbol RV-ratio hook (useOptionsExposure mold, plus the scan swap):
 * GET on mount/symbol change; a missing or session-stale snapshot fires
 * exactly ONE synchronous scan POST whose response IS the fresh payload.
 * Stale-but-present data keeps rendering underneath the scan, and scan
 * errors are suppressed while any data is displayed (house SWR behavior).
 * No polling loop exists anywhere.
 */
export function useRvRatio(symbol: string): UseRvRatioResult {
  const [data, setData] = useState<RvRatioPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const scanInFlightRef = useRef(false);
  const hasDisplayedDataRef = useRef(false);
  const normalizedSymbol = symbol.trim().toUpperCase();
  const endpoint = `/api/options/rv-ratio?symbol=${normalizedSymbol}`;

  const acceptPayload = useCallback((payload: RvRatioPayload) => {
    hasDisplayedDataRef.current = true;
    setData(payload);
    setError(null);
  }, []);

  const runScan = useCallback(
    async (parent: AbortController) => {
      if (scanInFlightRef.current) return;
      scanInFlightRef.current = true;
      setScanning(true);
      try {
        const response = await postScan(endpoint, parent);
        if (parent.signal.aborted) return;
        if (!response.ok) {
          throw new Error(`RV ratio scan failed (HTTP ${response.status})`);
        }
        const payload: unknown = await response.json();
        if (parent.signal.aborted) return;
        if (isRvRatioPayload(payload)) {
          acceptPayload(payload);
        } else if (!hasDisplayedDataRef.current && !isRvRatioMissingPayload(payload)) {
          // Cooldown or another non-payload variant with nothing on screen.
          setError("RV ratio measurement is not ready yet");
        }
      } catch (cause) {
        if (parent.signal.aborted || hasDisplayedDataRef.current) return;
        setError(cause instanceof Error ? cause.message : "RV ratio scan failed");
      } finally {
        scanInFlightRef.current = false;
        if (!parent.signal.aborted) setScanning(false);
      }
    },
    [acceptPayload, endpoint],
  );

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);

    try {
      const response = await fetch(endpoint, {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`RV ratio is temporarily unavailable (HTTP ${response.status})`);
      }
      const payload: unknown = await response.json();
      if (controller.signal.aborted) return;
      if (isRvRatioPayload(payload)) {
        acceptPayload(payload);
        if (payload.stale === true) void runScan(controller);
      } else if (isRvRatioMissingPayload(payload)) {
        void runScan(controller);
      } else {
        throw new Error("RV ratio returned an invalid measurement");
      }
    } catch (cause) {
      if (controller.signal.aborted || hasDisplayedDataRef.current) return;
      setError(cause instanceof Error ? cause.message : "RV ratio is temporarily unavailable");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [acceptPayload, endpoint, runScan]);

  const refresh = useCallback(async () => {
    const controller = controllerRef.current ?? new AbortController();
    controllerRef.current = controller;
    await runScan(controller);
  }, [runScan]);

  useEffect(() => {
    hasDisplayedDataRef.current = false;
    setData(null);
    setError(null);
    setScanning(false);
    void load();
    return () => controllerRef.current?.abort();
  }, [load]);

  return { data, loading, scanning, error, refresh };
}

/**
 * One scan POST bracketed by the parent abort (symbol change / unmount) and
 * the shared RV_RATIO_SCAN_TIMEOUT_MS client budget.
 */
async function postScan(endpoint: string, parent: AbortController): Promise<Response> {
  const scanController = new AbortController();
  const abortScan = () => scanController.abort();
  if (parent.signal.aborted) abortScan();
  parent.signal.addEventListener("abort", abortScan);
  const timeout = setTimeout(abortScan, RV_RATIO_SCAN_TIMEOUT_MS);
  try {
    return await fetch(endpoint, {
      method: "POST",
      cache: "no-store",
      signal: scanController.signal,
    });
  } finally {
    clearTimeout(timeout);
    parent.signal.removeEventListener("abort", abortScan);
  }
}
