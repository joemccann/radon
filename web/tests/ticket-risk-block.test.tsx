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
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
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
    expect(cell(container, "ORDER BREAKEVENS")).toBe("242.02 / 282.98");
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
    const lines = [...svg!.querySelectorAll("[data-testid='payoff-breakeven']")];
    const labels = [...container.querySelectorAll<HTMLElement>("[data-testid='payoff-breakeven-label']")];
    expect(lines).toHaveLength(2);
    expect(labels.map((label) => label.textContent)).toEqual(["242.02", "282.98"]);
    const width = Number(svg!.getAttribute("viewBox")!.split(" ")[2]);
    lines.forEach((line, index) => {
      const x = Number(line.getAttribute("x1"));
      expect(labels[index].style.left).toBe(`${(x / width) * 100}%`);
    });
  });

  it("places each breakeven price under its dotted line", () => {
    const { container } = render(
      <TicketRiskBlock
        legs={[
          { action: "BUY", right: "C", strike: 350, quantity: 1 },
          { action: "SELL", right: "C", strike: 400, quantity: 1 },
        ]}
        netPremium={6.96}
        spot={370}
        maxGain={4304}
        maxLoss={696}
        maxLossUnbounded={false}
        marginRequirement={34800}
        fundsAfter={231515}
        total={34800}
        isCredit={false}
      />,
    );
    const svg = container.querySelector(".ticket-risk-payoff");
    expect(svg).toBeTruthy();
    const width = Number(svg!.getAttribute("viewBox")!.split(" ")[2]);
    const lines = [...svg!.querySelectorAll("[data-testid='payoff-breakeven']")];
    const labels = [...container.querySelectorAll<HTMLElement>("[data-testid='payoff-breakeven-label']")];
    expect(lines).toHaveLength(1);
    expect(labels).toHaveLength(1);
    expect(labels[0].textContent).toBe("356.96");
    const x = Number(lines[0].getAttribute("x1"));
    expect(x / width).toBeGreaterThan(0.2);
    expect(x / width).toBeLessThan(0.8);
    expect(labels[0].style.left).toBe(`${(x / width) * 100}%`);
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

it.each([NaN, Infinity, 1e308])('withholds the expiry curve when execution premium is unmeasured (%s)', netPremium => {
  const { container, getByText } = render(<TicketRiskBlock legs={STRANGLE} netPremium={netPremium} spot={261.34} maxGain={null} maxLoss={null} maxLossUnbounded={false} marginRequirement={null} fundsAfter={null} total={null} isCredit={false} />);
  expect(container.querySelector('svg')).toBeNull();
  expect(getByText('Expiry payoff cannot be measured from the current price and legs.')).toBeTruthy();
  expect(container.innerHTML).not.toMatch(/(?:NaN|Infinity)/);
});


it('withholds nonfinite break-even labels when a finite spot overflows the payoff range', () => {
  const { container, getByText } = render(<TicketRiskBlock legs={[{ action: 'BUY', right: 'C', strike: 100, quantity: 1 }]} netPremium={1} spot={1.6e308} maxGain={null} maxLoss={null} maxLossUnbounded={false} marginRequirement={null} fundsAfter={null} total={null} isCredit={false} />);
  expect(cell(container, 'ORDER BREAKEVENS')).toBe('---');
  expect(container.querySelector('svg')).toBeNull();
  expect(getByText('Expiry payoff cannot be measured from the current price and legs.')).toBeTruthy();
  expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
});

it("centers each breakeven price on the marker x", () => {
  const css = readFileSync(
    path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../app/globals.css"),
    "utf8",
  );
  const axis = css.match(/\.ticket-risk-payoff-axis\s*\{([^}]*)\}/)?.[1] ?? "";
  const label = css.match(/\.ticket-risk-payoff-axis span\s*\{([^}]*)\}/)?.[1] ?? "";
  expect(axis).toContain("position: relative");
  expect(axis).not.toContain("space-between");
  expect(label).toContain("position: absolute");
  expect(label).toContain("transform: translateX(-50%)");
  expect(label).toContain("white-space: nowrap");
});
