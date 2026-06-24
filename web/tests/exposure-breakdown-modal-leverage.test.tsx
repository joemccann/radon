/**
 * @vitest-environment jsdom
 *
 * Component test: ExposureBreakdownModal renders the new
 * delta-adjusted leverage block when the user opens the
 * Dollar Delta card.
 *
 * The block must:
 *   - render only for the dollarDelta metric (not netLong, netShort, netExposure)
 *   - hide entirely when nlv is missing/zero (no NaN, no Infinity)
 *   - color-code by directional sign (long/short/neutral via CSS class)
 *   - cite NLV exactly so the user can verify the math
 *   - mention an APPROX-leg disclosure when any breakdown row uses approx delta
 */

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import ExposureBreakdownModal from "../components/ExposureBreakdownModal";
import type { ExposureDataWithBreakdown } from "../lib/exposureBreakdown";

afterEach(cleanup);

function makeRow(
  overrides: Partial<ExposureDataWithBreakdown["rows"][number]> = {},
): ExposureDataWithBreakdown["rows"][number] {
  return {
    positionId: 1,
    ticker: "AAPL",
    structure: "Long Call $270",
    spot: 260,
    delta: 1100,
    dollarDelta: 286_000,
    marketValue: 50_000,
    deltaSource: "ib",
    legs: [
      { type: "Call", direction: "LONG", strike: 270, contracts: 1, rawDelta: 0.5, legDelta: 50 },
    ],
    ...overrides,
  };
}

function makeExposure(
  overrides: Partial<ExposureDataWithBreakdown> = {},
): ExposureDataWithBreakdown {
  return {
    netLong: 500_000,
    netShort: 0,
    dollarDelta: 286_059,
    netExposurePct: 17.7,
    rows: [makeRow()],
    ...overrides,
  };
}

describe("ExposureBreakdownModal — delta-adjusted leverage block", () => {
  it("renders the leverage block with pct, multiplier, and NLV reference for long-biased exposure", () => {
    const exposure = makeExposure({ dollarDelta: 286_059 });

    render(
      <ExposureBreakdownModal
        metric="dollarDelta"
        exposure={exposure}
        bankroll={1_611_889.79}
        netLiquidation={1_611_889.79}
        onClose={() => {}}
      />,
    );

    const block = screen.getByTestId("dd-leverage-block");
    expect(block).toBeTruthy();

    // Multiplier (left)
    expect(within(block).getByTestId("dd-leverage-multiplier").textContent).toBe("0.18x");
    // Percentage (right)
    expect(within(block).getByTestId("dd-leverage-pct").textContent).toBe("+17.7%");
    // Bias label
    expect(within(block).getByTestId("dd-leverage-bias").textContent).toMatch(/long-biased/i);
    // Long-biased class for color
    expect(block.className).toMatch(/dd-leverage-long/);
    // Interpretation line
    expect(within(block).getByTestId("dd-leverage-interpretation").textContent).toMatch(
      /\$0\.18 of directional exposure/,
    );
    // NLV reference
    expect(within(block).getByTestId("dd-leverage-nlv").textContent).toMatch(/\$1,611,889\.79/);
  });

  it("colors short-biased exposure with the short class", () => {
    const exposure = makeExposure({ dollarDelta: -322_377.96 });

    render(
      <ExposureBreakdownModal
        metric="dollarDelta"
        exposure={exposure}
        bankroll={1_611_889.79}
        netLiquidation={1_611_889.79}
        onClose={() => {}}
      />,
    );

    const block = screen.getByTestId("dd-leverage-block");
    expect(block.className).toMatch(/dd-leverage-short/);
    expect(within(block).getByTestId("dd-leverage-multiplier").textContent).toBe("-0.20x");
    expect(within(block).getByTestId("dd-leverage-pct").textContent).toBe("-20.0%");
    expect(within(block).getByTestId("dd-leverage-bias").textContent).toMatch(/short-biased/i);
  });

  it("preserves the negative sign on compact dollar-delta row values", () => {
    const exposure = makeExposure({
      dollarDelta: -1_930_000,
      rows: [
        makeRow({
          ticker: "SPY",
          structure: "Bear Put Spread $650.0/$740.0",
          delta: -2621,
          dollarDelta: -1_930_000,
          deltaSource: "approx",
          legs: [
            { type: "Put", direction: "SHORT", strike: 650, contracts: 50, rawDelta: 0.02, legDelta: 100 },
            { type: "Put", direction: "LONG", strike: 740, contracts: 50, rawDelta: -0.54, legDelta: -2700 },
          ],
        }),
      ],
    });

    render(
      <ExposureBreakdownModal
        metric="dollarDelta"
        exposure={exposure}
        bankroll={1_611_889.79}
        netLiquidation={1_611_889.79}
        onClose={() => {}}
      />,
    );

    const row = screen.getByText("Bear Put Spread $650.0/$740.0").closest("tr");
    expect(row?.textContent ?? "").toContain("-$1.93M");
    expect(document.querySelector(".dd-leverage-dollar-delta")?.textContent ?? "").toContain(
      "$ Delta -$1.93M",
    );
  });

  it("renders 'Neutral' label when dollar delta is round-tripping near zero", () => {
    const exposure = makeExposure({ dollarDelta: 100 }); // 100/1.6M = 0.006%

    render(
      <ExposureBreakdownModal
        metric="dollarDelta"
        exposure={exposure}
        bankroll={1_611_889.79}
        netLiquidation={1_611_889.79}
        onClose={() => {}}
      />,
    );

    const block = screen.getByTestId("dd-leverage-block");
    expect(block.className).toMatch(/dd-leverage-neutral/);
    expect(within(block).getByTestId("dd-leverage-bias").textContent).toMatch(/market-neutral/i);
  });

  it("hides the leverage block entirely when netLiquidation is missing", () => {
    const exposure = makeExposure({ dollarDelta: 286_059 });

    render(
      <ExposureBreakdownModal
        metric="dollarDelta"
        exposure={exposure}
        bankroll={1_611_889.79}
        netLiquidation={undefined}
        onClose={() => {}}
      />,
    );

    expect(screen.queryByTestId("dd-leverage-block")).toBeNull();
  });

  it("hides the leverage block when netLiquidation is zero (no Infinity)", () => {
    render(
      <ExposureBreakdownModal
        metric="dollarDelta"
        exposure={makeExposure({ dollarDelta: 286_059 })}
        bankroll={1_611_889.79}
        netLiquidation={0}
        onClose={() => {}}
      />,
    );

    expect(screen.queryByTestId("dd-leverage-block")).toBeNull();
    // No stray Infinity or NaN ever leaks to the DOM
    const root = document.body.textContent ?? "";
    expect(root).not.toMatch(/Infinity/);
    expect(root).not.toMatch(/NaN/);
  });

  it("does not render the leverage block on netLong, netShort, or netExposure metrics", () => {
    for (const metric of ["netLong", "netShort", "netExposure"] as const) {
      cleanup();
      render(
        <ExposureBreakdownModal
          metric={metric}
          exposure={makeExposure({ dollarDelta: 286_059 })}
          bankroll={1_611_889.79}
          netLiquidation={1_611_889.79}
          onClose={() => {}}
        />,
      );
      expect(screen.queryByTestId("dd-leverage-block")).toBeNull();
    }
  });

  it("includes an APPROX disclosure when any row uses approx delta", () => {
    const exposure = makeExposure({
      dollarDelta: 286_059,
      rows: [
        makeRow({ deltaSource: "ib" }),
        makeRow({ positionId: 2, ticker: "MSFT", deltaSource: "approx" }),
      ],
    });

    render(
      <ExposureBreakdownModal
        metric="dollarDelta"
        exposure={exposure}
        bankroll={1_611_889.79}
        netLiquidation={1_611_889.79}
        onClose={() => {}}
      />,
    );

    expect(screen.getByTestId("dd-leverage-approx").textContent).toMatch(/approx/i);
  });

  it("does not render the APPROX disclosure when every row uses IB delta", () => {
    const exposure = makeExposure({
      dollarDelta: 286_059,
      rows: [makeRow({ deltaSource: "ib" }), makeRow({ positionId: 2, deltaSource: "ib" })],
    });

    render(
      <ExposureBreakdownModal
        metric="dollarDelta"
        exposure={exposure}
        bankroll={1_611_889.79}
        netLiquidation={1_611_889.79}
        onClose={() => {}}
      />,
    );

    expect(screen.queryByTestId("dd-leverage-approx")).toBeNull();
  });

  it("formula box mentions the leverage equation alongside the dollar delta sum", () => {
    render(
      <ExposureBreakdownModal
        metric="dollarDelta"
        exposure={makeExposure({ dollarDelta: 286_059 })}
        bankroll={1_611_889.79}
        netLiquidation={1_611_889.79}
        onClose={() => {}}
      />,
    );

    const formula = document.querySelector(".eb-formula code");
    expect(formula).toBeTruthy();
    const text = formula!.textContent ?? "";
    expect(text).toMatch(/Dollar Delta = SUM\( position_delta x spot_price \)/);
    expect(text).toMatch(/Leverage = Dollar Delta \/ Net Liquidation Value/);
  });
});
