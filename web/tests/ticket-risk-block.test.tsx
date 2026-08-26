// @vitest-environment jsdom
//
// The ticket rail renders risk ABOVE the CTA on purpose, so unbounded loss is
// read before the transmit button rather than after it.
//
// Honesty rule: every cell shows a real number or "---". Nothing here may be
// invented. P(PROFIT) needs a volatility model that the order pipeline does
// not currently produce, so it renders "---" until one is wired, rather than a
// plausible-looking guess an operator might size a position against.

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";

import TicketRiskBlock from "@/components/ticker-detail/TicketRiskBlock";
import type { PayoffLeg } from "@/lib/order/payoff";

afterEach(cleanup);

const STRANGLE: PayoffLeg[] = [
  { action: "SELL", right: "P", strike: 245, quantity: 1 },
  { action: "SELL", right: "C", strike: 280, quantity: 1 },
];

function cell(container: HTMLElement, label: string): string {
  const node = [...container.querySelectorAll(".ticket-risk-cell")].find(
    (el) => (el.querySelector(".ticket-risk-cell-label")?.textContent ?? "").trim() === label,
  );
  if (!node) throw new Error(`no risk cell labelled ${label}`);
  return (node.querySelector(".ticket-risk-cell-value")?.textContent ?? "").trim();
}

describe("TicketRiskBlock", () => {
  it("renders an unbounded short strangle honestly", () => {
    const { container } = render(
      <TicketRiskBlock
        legs={STRANGLE}
        netPremium={-2.98}
        spot={261.34}
        maxGain={298}
        maxLoss={null}
        maxLossUnbounded
        marginRequirement={4120}
        fundsAfter={764183}
        total={298}
        totalLabel="TOTAL"
        isCredit
      />,
    );

    expect(cell(container, "MAX GAIN")).toBe("$298.00");
    expect(cell(container, "MAX LOSS")).toBe("UNBOUNDED");
    expect(cell(container, "MARGIN REQ")).toBe("$4,120");
    // Derived from exact payoff arithmetic, not from the design's screenshot.
    expect(cell(container, "BREAKEVENS")).toBe("242.02 / 282.98");
  });

  it("shows --- for probability of profit rather than inventing one", () => {
    const { container } = render(
      <TicketRiskBlock
        legs={STRANGLE}
        netPremium={-2.98}
        spot={261.34}
        maxGain={298}
        maxLoss={null}
        maxLossUnbounded
        marginRequirement={4120}
        fundsAfter={764183}
        total={298}
        isCredit
      />,
    );
    expect(cell(container, "P(PROFIT)")).toBe("---");
  });

  it("degrades every unavailable figure to --- instead of zero", () => {
    const { container } = render(
      <TicketRiskBlock
        legs={STRANGLE}
        netPremium={-2.98}
        spot={261.34}
        maxGain={null}
        maxLoss={null}
        maxLossUnbounded={false}
        marginRequirement={null}
        fundsAfter={null}
        total={null}
        isCredit
      />,
    );
    expect(cell(container, "MAX GAIN")).toBe("---");
    expect(cell(container, "MAX LOSS")).toBe("---");
    expect(cell(container, "MARGIN REQ")).toBe("---");
    expect(container.textContent).not.toContain("$0.00");
  });

  it("draws the payoff curve with a marker per breakeven", () => {
    const { container } = render(
      <TicketRiskBlock
        legs={STRANGLE}
        netPremium={-2.98}
        spot={261.34}
        maxGain={298}
        maxLoss={null}
        maxLossUnbounded
        marginRequirement={4120}
        fundsAfter={764183}
        total={298}
        isCredit
      />,
    );
    const svg = container.querySelector(".ticket-risk-payoff");
    expect(svg).toBeTruthy();
    expect(svg!.querySelector("polyline")).toBeTruthy();
    expect(svg!.querySelectorAll("[data-testid='payoff-breakeven']")).toHaveLength(2);
  });

  it("omits the curve when there are no legs to price", () => {
    const { container } = render(
      <TicketRiskBlock
        legs={[]}
        netPremium={0}
        spot={261.34}
        maxGain={null}
        maxLoss={null}
        maxLossUnbounded={false}
        marginRequirement={null}
        fundsAfter={null}
        total={null}
        isCredit={false}
      />,
    );
    expect(container.querySelector(".ticket-risk-payoff")).toBeNull();
  });
});
