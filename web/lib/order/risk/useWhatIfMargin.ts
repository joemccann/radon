"use client";

/**
 * useWhatIfMargin — async layer over the synchronous `useOrderRisk` chokepoint.
 *
 * Fires a real IB what-if margin round-trip ONLY for a genuine undefined-risk
 * multi-leg combo whose client-side Reg-T estimate came back null (the
 * "UNAVAILABLE — IB what-if required" case). Lives in `OrderRiskGate`, so every
 * order surface inherits it. The margin is INFORMATIONAL — it never flips
 * `okToSubmit`.
 *
 * Gated end-to-end behind `NEXT_PUBLIC_WHATIF_MARGIN_ENABLED` (passed as
 * `enabled`): when off it stays idle and never fetches, so the backend can ship
 * inert and bake before being turned on.
 */
import { useEffect, useRef, useState } from "react";
import type { OrderRiskInput, OrderRiskState } from "./useOrderRisk";
import { whatIfKey, buildWhatIfBody } from "./internal/whatIfKey";

export type WhatIfMarginStatus = "idle" | "loading" | "success" | "error";

export interface WhatIfMarginResult {
  status: WhatIfMarginStatus;
  initMargin: number | null;
}

const DEBOUNCE_MS = 400;

const IDLE: WhatIfMarginResult = { status: "idle", initMargin: null };

export function useWhatIfMargin(
  input: OrderRiskInput | null,
  state: OrderRiskState | null,
  enabled: boolean,
): WhatIfMarginResult {
  const [result, setResult] = useState<WhatIfMarginResult>(IDLE);
  const cacheRef = useRef<Map<string, number>>(new Map());

  const marginImpact = state?.summary.marginImpact ?? null;
  const eligible =
    enabled &&
    state?.coverageStatus === "resolved" &&
    marginImpact != null &&
    marginImpact.requirement == null;
  // Structural key (no price) — refetch only when the position changes.
  const key = eligible ? whatIfKey(input) : null;

  useEffect(() => {
    if (key == null || input == null) {
      setResult(IDLE);
      return;
    }

    const cached = cacheRef.current.get(key);
    if (cached !== undefined) {
      setResult({ status: "success", initMargin: cached });
      return;
    }

    const body = buildWhatIfBody(input);
    if (body == null) {
      setResult(IDLE);
      return;
    }

    const controller = new AbortController();
    let cancelled = false;
    setResult({ status: "loading", initMargin: null });

    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const res = await fetch("/api/orders/whatif", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
            signal: controller.signal,
          });
          if (!res.ok) throw new Error(`whatif ${res.status}`);
          const data = (await res.json()) as { initMargin?: number | null };
          if (cancelled) return;
          const im = typeof data.initMargin === "number" ? data.initMargin : null;
          if (im != null) {
            cacheRef.current.set(key, im);
            setResult({ status: "success", initMargin: im });
          } else {
            // No retry loop — a withheld margin (off-hours / awaiting-2FA)
            // reverts to today's UNAVAILABLE text rather than hammering IB.
            setResult({ status: "error", initMargin: null });
          }
        } catch {
          if (!cancelled) setResult({ status: "error", initMargin: null });
        }
      })();
    }, DEBOUNCE_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      controller.abort();
    };
    // `key` encodes the structural identity of `input`; refetch only on a real
    // structural change, never on price keystrokes or unrelated re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return result;
}
