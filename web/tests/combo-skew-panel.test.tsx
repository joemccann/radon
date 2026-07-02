/**
 * @vitest-environment jsdom
 */
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import ComboSkewPanel from "../components/ComboSkewPanel";
import type { OrderLeg } from "../lib/optionsChainUtils";
import type { PriceData } from "../lib/pricesProtocol";

afterEach(cleanup);

function leg(right: "C" | "P", strike: number, action: "BUY" | "SELL" = "SELL"): OrderLeg {
  return {
    id: `MU_20260717_${strike}_${right}`,
    action,
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

describe("ComboSkewPanel", () => {
  it("renders the short-strangle skew telemetry contract", () => {
    render(
      <ComboSkewPanel
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
    expect(screen.getByText("STRANGLE SKEW")).toBeTruthy();
    for (const label of ["CALL IV", "PUT IV", "IV SKEW", "CALL Δ", "PUT Δ", "NET Δ", "SRC"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.getByText("30.0%")).toBeTruthy();
    expect(screen.getByText("42.0%")).toBeTruthy();
    expect(screen.getByText("+12.0 pt")).toBeTruthy();
    expect(screen.getByText("-0.184")).toBeTruthy();
    expect(screen.getByText("+0.231")).toBeTruthy();
    expect(screen.getByText("+5 sh")).toBeTruthy();
    expect(screen.getAllByText("SHORT LEG").length).toBe(2);
    expect(screen.getAllByText("STREAM").length).toBeGreaterThan(0);
  });

  it("renders the skew telemetry for a bullish risk reversal", () => {
    render(
      <ComboSkewPanel
        ticker="MU"
        legs={[leg("P", 850, "SELL"), leg("C", 1250, "BUY")]}
        spot={1050}
        prices={{
          MU_20260717_850_P: pd({ symbol: "MU_20260717_850_P", impliedVol: 0.42, delta: -0.231 }),
          MU_20260717_1250_C: pd({ symbol: "MU_20260717_1250_C", impliedVol: 0.30, delta: 0.184 }),
        }}
      />,
    );

    expect(screen.getByTestId("short-strangle-skew-panel")).toBeTruthy();
    expect(screen.getByText("RISK REVERSAL SKEW")).toBeTruthy();
    expect(screen.getByText("30.0%")).toBeTruthy();
    expect(screen.getByText("42.0%")).toBeTruthy();
    expect(screen.getByText("+12.0 pt")).toBeTruthy();
    // Long call keeps its +delta, short put flips to +
    expect(screen.getByText("+0.184")).toBeTruthy();
    expect(screen.getByText("+0.231")).toBeTruthy();
    expect(screen.getByText("LONG LEG")).toBeTruthy();
    expect(screen.getByText("SHORT LEG")).toBeTruthy();
    expect(screen.getByText("+42 sh")).toBeTruthy();
  });

  it("does not render for order proposals without a call+put skew pair", () => {
    const { container } = render(
      <ComboSkewPanel
        ticker="MU"
        legs={[{ ...leg("C", 850), action: "BUY" }, leg("C", 1250)]}
        spot={1050}
        prices={{}}
      />,
    );
    expect(container.textContent).toBe("");
  });
});
