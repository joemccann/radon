/**
 * @vitest-environment jsdom
 *
 * P2 (tasks/mobile-parity-audit.md): flow sparklines were desktop-only with
 * hover-title tooltips. The mobile variant must be finger-readable: tapping a
 * bar reveals that day's buy ratio in a caption (no hover required).
 */

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import MobileFlowSparkline from "../components/mobile/MobileFlowSparkline";

const RATIOS = [
  { date: "2026-06-27", buy_ratio: 0.62 },
  { date: "2026-06-30", buy_ratio: 0.4 },
  { date: "2026-07-01", buy_ratio: null },
];

afterEach(cleanup);

describe("MobileFlowSparkline", () => {
  it("renders one bar per ratio", () => {
    const { container } = render(<MobileFlowSparkline ratios={RATIOS} />);
    expect(container.querySelectorAll("rect").length).toBe(3);
  });

  it("shows the series label before any tap", () => {
    render(<MobileFlowSparkline ratios={RATIOS} />);
    expect(screen.getByTestId("mobile-flow-sparkline-caption").textContent).toBe("5D BUY RATIO");
  });

  it("tap reveals the tapped day's date and buy percent", () => {
    render(<MobileFlowSparkline ratios={RATIOS} />);
    const svg = screen.getByTestId("mobile-flow-sparkline-svg");

    fireEvent.pointerDown(svg, { clientX: 1 });
    expect(screen.getByTestId("mobile-flow-sparkline-caption").textContent).toBe("06-27 · 62%");

    fireEvent.pointerDown(svg, { clientX: 8 });
    expect(screen.getByTestId("mobile-flow-sparkline-caption").textContent).toBe("06-30 · 40%");
  });

  it("tap on a null-ratio day shows a placeholder value", () => {
    render(<MobileFlowSparkline ratios={RATIOS} />);
    fireEvent.pointerDown(screen.getByTestId("mobile-flow-sparkline-svg"), { clientX: 15 });
    expect(screen.getByTestId("mobile-flow-sparkline-caption").textContent).toBe("07-01 · ---");
  });

  it("renders nothing without ratios", () => {
    const { container } = render(<MobileFlowSparkline ratios={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
