/**
 * @vitest-environment jsdom
 *
 * The flow-analysis "analyzing" state shipped a one-off border spinner and a
 * title that repeated the hero badge verbatim ("ANALYZING ADBE"). Every other
 * loading surface in the app uses <SpectralLoader>. Pin the shared loader and
 * the de-duplicated copy so the bespoke spinner cannot come back.
 */

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/useTickerFlowReport", () => ({
  useTickerFlowReport: () => ({
    data: null,
    status: "scanning",
    error: null,
    refresh: () => {},
  }),
}));

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, isTablet: false, hasMounted: true }),
}));

import TickerFlowReport from "../components/flow-analysis/TickerFlowReport";

afterEach(cleanup);

describe("flow-analysis analyzing state", () => {
  it("renders the shared SpectralLoader, not a one-off spinner", () => {
    const { container } = render(<TickerFlowReport ticker="NVDA" />);

    expect(container.querySelector(".spectral-loader")).not.toBeNull();
    expect(container.querySelector(".ticker-flow-analyzing-spinner")).toBeNull();
  });

  it("does not repeat the hero badge headline in the panel below it", () => {
    render(<TickerFlowReport ticker="NVDA" />);

    expect(screen.getAllByText(/Analyzing NVDA/i)).toHaveLength(1);
  });

  it("keeps the reconstruction steps as supporting context", () => {
    const { container } = render(<TickerFlowReport ticker="NVDA" />);

    expect(container.querySelectorAll(".ticker-flow-analyzing-steps li")).toHaveLength(4);
  });
});
