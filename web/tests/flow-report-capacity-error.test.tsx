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
