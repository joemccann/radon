/**
 * @vitest-environment jsdom
 *
 * Leveraged payoff surfacing: a spread carries embedded leverage, and the
 * operator needs to read "is this a 7:1 or a 1.2:1" BEFORE execution rather
 * than dividing Max Gain by Max Loss in their head at the confirm step.
 *
 * The ratio is also the Gate 1 (convexity) test — gain must be >= 2x loss —
 * so the rendered value doubles as the gate readout.
 */
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { OrderConfirmSummary } from "../lib/order/components/OrderConfirmSummary";
import { computePayoffRatio, formatPayoffRatio } from "../lib/order/payoffRatio";
import type { OrderPresentationSummary } from "../lib/order/types";
import { brandAugmentedSummaryForTest } from "../lib/order/risk/__test_only__";

const augment = brandAugmentedSummaryForTest;

afterEach(cleanup);

describe("computePayoffRatio", () => {
  it("returns the gain:loss multiple for a bounded spread", () => {
    // The user's live bull call spread: $329,400 gain over $45,600 loss.
    const result = computePayoffRatio({
      maxGain: 329_400,
      maxLoss: 45_600,
    });
    expect(result?.kind).toBe("ratio");
    if (result?.kind !== "ratio") throw new Error("expected ratio");
    expect(result.ratio).toBeCloseTo(7.224, 2);
    expect(result.meetsConvexity).toBe(true);
  });

  it("flags a sub-2x structure as failing Gate 1 convexity", () => {
    const result = computePayoffRatio({ maxGain: 1_500, maxLoss: 1_000 });
    expect(result?.kind).toBe("ratio");
    if (result?.kind !== "ratio") throw new Error("expected ratio");
    expect(result.ratio).toBeCloseTo(1.5, 5);
    expect(result.meetsConvexity).toBe(false);
  });

  it("treats exactly 2:1 as meeting convexity (gain >= 2x loss)", () => {
    const result = computePayoffRatio({ maxGain: 2_000, maxLoss: 1_000 });
    if (result?.kind !== "ratio") throw new Error("expected ratio");
    expect(result.meetsConvexity).toBe(true);
  });

  it("reports uncapped upside when max gain is unbounded but loss is capped", () => {
    const result = computePayoffRatio({
      maxGain: null,
      maxGainUnbounded: true,
      maxLoss: 5_000,
    });
    expect(result?.kind).toBe("uncapped");
  });

  it("reports undefined risk when max loss is unbounded (no meaningful multiple)", () => {
    const result = computePayoffRatio({
      maxGain: 5_000,
      maxLoss: null,
      maxLossUnbounded: true,
    });
    expect(result?.kind).toBe("undefined-risk");
  });

  it("returns null when the structure carries no risk numbers (close-out)", () => {
    expect(computePayoffRatio({ maxGain: null, maxLoss: null })).toBeNull();
  });

  it("returns null rather than dividing by a zero max loss", () => {
    expect(computePayoffRatio({ maxGain: 1_000, maxLoss: 0 })).toBeNull();
  });

  it("formats the multiple against a 1 base", () => {
    expect(formatPayoffRatio(7.224)).toBe("7.2 : 1");
    expect(formatPayoffRatio(2)).toBe("2 : 1");
    expect(formatPayoffRatio(12.35)).toBe("12.4 : 1");
  });
});

describe("OrderConfirmSummary — leveraged payoff row", () => {
  const spread: OrderPresentationSummary = {
    description: "Bull Call Spread @ $0.54",
    totalCost: 40_500,
    maxGain: 329_400,
    maxLoss: 45_600,
  };

  it("renders the payoff multiple for the bull call spread", () => {
    const { getByTestId, container } = render(
      <OrderConfirmSummary summary={augment(spread)} variant="info" />,
    );
    const payoff = getByTestId("order-payoff-ratio");
    expect(payoff.textContent).toMatch(/7\.2 : 1/);
    expect(container.textContent).toMatch(/Payoff:/);
  });

  it("marks a >=2x structure as convex", () => {
    const { getByTestId } = render(
      <OrderConfirmSummary summary={augment(spread)} variant="info" />,
    );
    expect(getByTestId("order-payoff-ratio").getAttribute("data-meets-convexity")).toBe("true");
  });

  it("marks a sub-2x structure as failing convexity and names the gate", () => {
    const thin: OrderPresentationSummary = {
      ...spread,
      maxGain: 1_500,
      maxLoss: 1_000,
    };
    const { getByTestId } = render(
      <OrderConfirmSummary summary={augment(thin)} variant="info" />,
    );
    const payoff = getByTestId("order-payoff-ratio");
    expect(payoff.getAttribute("data-meets-convexity")).toBe("false");
    expect(payoff.textContent).toMatch(/GATE 1/i);
  });

  it("renders UNCAPPED when upside is unbounded against a capped loss", () => {
    const longCall: OrderPresentationSummary = {
      description: "Long Call @ $2.00",
      totalCost: 2_000,
      maxGain: null,
      maxGainUnbounded: true,
      maxLoss: 2_000,
    };
    const { getByTestId } = render(
      <OrderConfirmSummary summary={augment(longCall)} variant="info" />,
    );
    expect(getByTestId("order-payoff-ratio").textContent).toMatch(/UNCAPPED/i);
  });

  it("does not render a multiple when max loss is unbounded", () => {
    const naked: OrderPresentationSummary = {
      description: "Short Call @ $5.00",
      totalCost: -500,
      maxGain: 500,
      maxLoss: null,
      maxLossUnbounded: true,
      undefinedRiskReason: "Naked short call",
    };
    const { queryByTestId } = render(
      <OrderConfirmSummary summary={augment(naked)} variant="info" />,
    );
    expect(queryByTestId("order-payoff-ratio")).toBeNull();
  });

  it("omits the payoff row entirely for a close-out summary", () => {
    const close: OrderPresentationSummary = {
      description: "Close 10x NVDA Call @ $4.00",
      totalCost: 4_000,
      totalLabel: "Proceeds:",
      estimatedPnl: 1_200,
    };
    const { queryByTestId } = render(
      <OrderConfirmSummary summary={augment(close)} variant="info" />,
    );
    expect(queryByTestId("order-payoff-ratio")).toBeNull();
  });
});
