/**
 * @vitest-environment jsdom
 */
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import ShortStrangleSkewPanel from "../components/ShortStrangleSkewPanel";
import type { OrderLeg } from "../lib/optionsChainUtils";
import type { PriceData } from "../lib/pricesProtocol";

afterEach(cleanup);

function leg(right: "C" | "P", strike: number): OrderLeg {
  return {
    id: `MU_20260717_${strike}_${right}`,
    action: "SELL",
    right,
    strike,
    expiry: "20260717",
    quantity: 1,
    limitPrice: null,
  };
}

function pd(overrides: Partial<PriceData>): PriceData {
  return {
    symbol: overrides.symbol ?? "MU",
    last: null,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: "2026-06-24T16:00:00.000Z",
    ...overrides,
  };
}

describe("ShortStrangleSkewPanel", () => {
  it("renders the short-strangle skew telemetry contract", () => {
    render(
      <ShortStrangleSkewPanel
        ticker="MU"
        legs={[leg("P", 850), leg("C", 1250)]}
        spot={1050}
        prices={{
          MU_20260717_850_P: pd({ symbol: "MU_20260717_850_P", impliedVol: 0.42, delta: -0.231 }),
          MU_20260717_1250_C: pd({ symbol: "MU_20260717_1250_C", impliedVol: 0.30, delta: 0.184 }),
        }}
      />,
    );

    expect(screen.getByTestId("short-strangle-skew-panel")).toBeTruthy();
    for (const label of ["CALL IV", "PUT IV", "IV SKEW", "CALL Δ", "PUT Δ", "NET Δ", "SRC"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.getByText("30.0%")).toBeTruthy();
    expect(screen.getByText("42.0%")).toBeTruthy();
    expect(screen.getByText("+12.0 pt")).toBeTruthy();
    expect(screen.getByText("-0.184")).toBeTruthy();
    expect(screen.getByText("+0.231")).toBeTruthy();
    expect(screen.getByText("+5 sh")).toBeTruthy();
    expect(screen.getAllByText("STREAM").length).toBeGreaterThan(0);
  });

  it("does not render for non-strangle order proposals", () => {
    const { container } = render(
      <ShortStrangleSkewPanel
        ticker="MU"
        legs={[{ ...leg("P", 850), action: "BUY" }, leg("C", 1250)]}
        spot={1050}
        prices={{}}
      />,
    );
    expect(container.textContent).toBe("");
  });
});
