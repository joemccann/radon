/**
 * @vitest-environment jsdom
 *
 * 2026-08-27: /flow-analysis/JOBY showed ANALYZING plus the raw
 * `Radon API 502: Subprocess capacity exhausted` string when the general
 * subprocess lane was full. Capacity shed is retryable, not a finished
 * analysis — the hero must not keep saying ANALYZING, and the operator
 * copy must not leak the FastAPI 502.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  flowReportErrorCopy,
  isSubprocessCapacityError,
} from "@/lib/flowReportError";

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, isTablet: false, hasMounted: true }),
}));

const hookState = vi.hoisted(() => ({
  data: null as null,
  status: "error" as const,
  error: "Radon API 502: Subprocess capacity exhausted",
  refresh: () => {},
}));

vi.mock("@/lib/useTickerFlowReport", () => ({
  useTickerFlowReport: () => hookState,
}));

import TickerFlowReport from "../components/flow-analysis/TickerFlowReport";

afterEach(cleanup);

describe("flowReportErrorCopy", () => {
  it("rewrites the FastAPI capacity 502", () => {
    expect(isSubprocessCapacityError("Radon API 502: Subprocess capacity exhausted")).toBe(true);
    expect(flowReportErrorCopy("Radon API 502: Subprocess capacity exhausted")).toBe(
      "Scan lane is full. Wait a moment and refresh.",
    );
  });

  it("leaves a real script failure intact", () => {
    expect(flowReportErrorCopy("Script timed out after 300s")).toBe("Script timed out after 300s");
  });
});

describe("ticker flow report capacity 502", () => {
  it("does not keep the ANALYZING hero after a capacity 502", () => {
    render(<TickerFlowReport ticker="JOBY" />);

    expect(screen.queryByText(/Analyzing JOBY/i)).toBeNull();
    expect(screen.getByRole("alert").textContent).toBe(
      "Scan lane is full. Wait a moment and refresh.",
    );
    expect(screen.getByRole("status").textContent).toMatch(/Scan failed/i);
  });
});

/* ── R-358 / REL-128: a preserved verdict must be marked, not presented ─────
 *
 * `showVerdict` deliberately includes `status === "error"`, and the hook
 * preserves prior data on POST failure by design, so on any failure that left
 * a cached verdict the hero rendered the direction label and rationale with NO
 * staleness marking and the body rendered identically to a fresh report. A 429
 * yielded a raw "Too Many Requests" strip above a hero still showing BULLISH
 * with yesterday's rationale, and the `Retry-After` the route sets was read by
 * nobody.
 */
describe("TickerFlowReport hero — a preserved verdict is marked stale", () => {
  const CACHED = {
    ticker: "JOBY",
    fetched_at: "2026-08-26T14:00:00Z",
    verdict: { direction: "BULLISH" as const, confidence: 74, rationale: "Sustained DP buying" },
  };

  function renderWith(state: Record<string, unknown>) {
    Object.assign(hookState, state);
    return render(<TickerFlowReport ticker="JOBY" />);
  }

  afterEach(() => {
    Object.assign(hookState, {
      data: null,
      status: "error",
      error: "Radon API 502: Subprocess capacity exhausted",
      refresh: () => {},
    });
  });

  it("marks the hero when an error leaves a cached verdict standing", () => {
    renderWith({ data: CACHED, status: "error", error: "Too Many Requests retry-after: 30" });
    expect(screen.getByTestId("flow-hero-stale")).toBeTruthy();
  });

  it("names the Retry-After the route set", () => {
    renderWith({ data: CACHED, status: "error", error: "Too Many Requests retry-after: 30" });
    expect(screen.getByTestId("flow-hero-stale-note").textContent).toContain("30s");
  });

  it("marks the hero for a degraded POST that returned 200", () => {
    renderWith({ data: CACHED, status: "stale", error: "Scan did not complete" });
    expect(screen.getByTestId("flow-hero-stale")).toBeTruthy();
  });

  it("does not mark a genuinely fresh verdict", () => {
    renderWith({ data: CACHED, status: "fresh", error: null });
    expect(screen.queryByTestId("flow-hero-stale")).toBeNull();
  });
});
