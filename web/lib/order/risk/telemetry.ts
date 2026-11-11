"use client";

/**
 * Per-session order-risk telemetry buffer.
 *
 * Each new `<OrderRiskGate>` traceId writes one record to a `sessionStorage` ring
 * buffer keyed by `radon:order-risk-traces`. The buffer keeps the most
 * recent 50 traces. Bug reports include the dump so a future "wrong
 * max-loss" filing can be cross-referenced against exactly which surface
 * produced the trace, what the inputs were, and what the chokepoint output.
 *
 * Purely client-local — no network calls. Survives the cache-no-store
 * contract because it lives entirely in the browser session.
 *
 * Dev console helper: paste `JSON.parse(sessionStorage.getItem("radon:order-risk-traces"))`
 * into DevTools to inspect.
 */

import { useEffect } from "react";
import type { AugmentedOrderSummary } from "../types";

const STORAGE_KEY = "radon:order-risk-traces";
const MAX_TRACES = 50;

export interface OrderRiskTrace {
  /** ISO8601 timestamp. */
  timestamp: string;
  /** Correlates with `summary.traceId`. */
  traceId: string;
  /** Surface tag passed to `<OrderRiskGate>` (e.g. "chain-builder"). */
  surface: string;
  /** Underlying symbol the order targets. */
  ticker: string;
  /** Number of chain legs the surface handed in. */
  legCount: number;
  /** Combo units after augmentation (GCD'd quantity). */
  comboQuantity: number;
  /** Resolution state of the portfolio when this trace was produced. */
  coverageStatus: AugmentedOrderSummary["coverageStatus"];
  /** Number of held positions injected as virtual coverage. */
  coveringLegCount: number;
  /** Stock-cover basis adjustment applied (per-share-per-combo, $). */
  netPremiumAdjustment: number;
  /** Final max-loss reported to the operator. `null` ⇒ UNBOUNDED. */
  maxLoss: number | null;
  /** True iff the model returned UNBOUNDED max loss. */
  maxLossUnbounded: boolean;
  /** True for any undefined-risk verdict (uncovered short call, naked short put, etc.). */
  hasUndefinedRisk: boolean;
}

function readBuffer(): OrderRiskTrace[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as OrderRiskTrace[]) : [];
  } catch {
    return [];
  }
}

function writeBuffer(traces: OrderRiskTrace[]): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(traces.slice(-MAX_TRACES)));
  } catch {
    // sessionStorage can throw on quota / disabled-storage; swallow silently
    // — telemetry is best-effort, must not crash the order flow.
  }
}

/**
 * Append a trace to the session buffer. Trimming to `MAX_TRACES` keeps the
 * storage footprint bounded (~10 KB worst-case).
 */
export function recordOrderRiskTrace(trace: OrderRiskTrace): void {
  const buf = readBuffer();
  buf.push(trace);
  writeBuffer(buf);
}

/** Returns a fresh copy of the buffer. Used by dev consoles + bug-report tooling. */
export function dumpOrderRiskTraces(): OrderRiskTrace[] {
  return readBuffer();
}

/** Clears the session buffer. Exposed for tests + manual debugging. */
export function clearOrderRiskTraces(): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* noop */
  }
}

/**
 * Internal hook used by `<OrderRiskGate>`. Records ONE trace per traceId
 * (which is regenerated on every meaningful state change inside the hook,
 * so this gives one record per resolved-state observation). Effect deps
 * include `traceId` so React's useEffect dedupe handles the no-op case.
 */
export function useRecordOrderRiskTrace(
  surface: string,
  summary: AugmentedOrderSummary | null,
  ticker: string,
  legCount: number,
  comboQuantity: number,
  coveringLegCount: number,
  netPremiumAdjustment: number,
  hasUndefinedRisk: boolean,
): void {
  const traceId = summary?.traceId ?? null;
  const coverageStatus = summary?.coverageStatus ?? null;
  const maxLoss = summary?.maxLoss ?? null;
  const maxLossUnbounded = summary?.maxLossUnbounded === true;
  useEffect(() => {
    if (!traceId || !coverageStatus) return;
    recordOrderRiskTrace({
      timestamp: new Date().toISOString(),
      traceId,
      surface,
      ticker,
      legCount,
      comboQuantity,
      coverageStatus,
      coveringLegCount,
      netPremiumAdjustment,
      maxLoss: maxLoss ?? null,
      maxLossUnbounded,
      hasUndefinedRisk,
    });
    // Effect runs once per unique traceId — `useOrderRisk` regenerates the
    // id on every memo recomputation, so this lines up with operator-visible
    // changes (typed a digit, flipped action, edited a leg).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [traceId]);
}
