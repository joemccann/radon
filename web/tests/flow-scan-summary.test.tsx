// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import FlowScanSummary, { flowFinding } from "../components/flow-analysis/FlowScanSummary";

afterEach(cleanup);

describe("flow analysis: collapse empty sections into one sentence", () => {
  it("states the finding when nothing supports or contradicts", () => {
    render(<FlowScanSummary counts={{ supports: 0, against: 0, neutral: 8, watch: 0 }} scanned={8} scannedAt="1:00:55 PM" />);
    expect(screen.getByRole("heading", { name: "No position has flow behind or against it." })).toBeDefined();
    expect(screen.getByText("8 positions scanned at 1:00:55 PM. All 8 read neutral or low signal. Nothing to watch.")).toBeDefined();
    for (const label of ["Supports", "Against", "Neutral", "Watch"]) expect(screen.getByText(label)).toBeDefined();
  });
  it("names the counts when flow exists", () => {
    expect(flowFinding({ supports: 2, against: 1, neutral: 5, watch: 0 })).toBe("2 positions have flow behind them, 1 against.");
    expect(flowFinding({ supports: 1, against: 0, neutral: 0, watch: 0 })).toBe("1 position has flow behind it, 0 against.");
  });
});
