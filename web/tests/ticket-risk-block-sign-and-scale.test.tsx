// @vitest-environment jsdom
//
// R-279 / R-280 (REL-096): the ticket risk block stops lying about sign and
// scale.
//
// (a) `usd()` absolute-valued everything, so a NEGATIVE "funds after" — the
//     order overdraws the account — rendered identically to the same amount
//     of spare cash. "$12,000 short" and "$12,000 spare" are opposite
//     decisions and must not be the same six characters.
// (b) The heading claimed "PER 1× COMBO" over cells the caller feeds from
//     the WHOLE-ORDER risk summary (max gain/loss, margin, funds after,
//     total). Only the payoff curve and its breakevens are per-combo. The
//     heading now states the scale each group actually carries.

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";

import TicketRiskBlock from "@/components/ticker-detail/TicketRiskBlock";
import type { PayoffLeg } from "@/lib/order/payoff";

afterEach(cleanup);

const SHORT_CALL: PayoffLeg[] = [{ action: "SELL", right: "C", strike: 100, quantity: 1 }];

function cell(container: HTMLElement, label: string): HTMLElement {
  const node = [...container.querySelectorAll(".ticket-risk-cell")].find(
    (el) => (el.querySelector(".ticket-risk-cell-label")?.textContent ?? "").trim() === label,
  );
  if (!node) throw new Error(`no risk cell labelled ${label}`);
  return node as HTMLElement;
}

function renderBlock(overrides: Partial<React.ComponentProps<typeof TicketRiskBlock>> = {}) {
  return render(
    <TicketRiskBlock
      legs={SHORT_CALL}
      netPremium={-2}
      spot={99}
      maxGain={200}
      maxLoss={null}
      maxLossUnbounded
      marginRequirement={20000}
      fundsAfter={80000}
      total={200}
      totalLabel="TOTAL"
      isCredit
      {...overrides}
    />,
  );
}

describe("FUNDS AFTER keeps its sign", () => {
  it("does not render an overdraft as if it were spare cash", () => {
    const { container } = renderBlock({ fundsAfter: -12000 });
    const node = cell(container, "FUNDS AFTER");
    const value = (node.querySelector(".ticket-risk-cell-value")?.textContent ?? "").trim();

    // Pre-fix this was exactly "$12,000" — the same string a $12,000 surplus
    // produces.
    expect(value).not.toBe("$12,000");
    expect(value).toMatch(/-|\(/);
    expect(node.querySelector(".ticket-risk-cell-value--loss")).toBeTruthy();
  });

  it("leaves a positive balance exactly as it was", () => {
    const { container } = renderBlock({ fundsAfter: 80000 });
    const node = cell(container, "FUNDS AFTER");
    expect((node.querySelector(".ticket-risk-cell-value")?.textContent ?? "").trim()).toBe("$80,000");
    expect(node.querySelector(".ticket-risk-cell-value--loss")).toBeFalsy();
  });

  it("still renders --- when the figure is unavailable", () => {
    const { container } = renderBlock({ fundsAfter: null });
    expect((cell(container, "FUNDS AFTER").querySelector(".ticket-risk-cell-value")?.textContent ?? "").trim())
      .toBe("---");
  });
});

describe("the heading states the scale each group carries", () => {
  it("does not claim the whole-order cells are per 1x combo", () => {
    const { container } = renderBlock();
    const head = (container.querySelector(".ticket-risk-head")?.textContent ?? "").trim();
    // The grid is fed from the whole-order risk summary; only the curve and
    // its breakevens are per-combo.
    expect(head).not.toMatch(/PER 1\D?\s*COMBO/i);
  });

  it("labels the payoff curve as the per-combo figure it is", () => {
    const { container } = renderBlock();
    const wrap = container.querySelector(".ticket-risk-payoff-wrap");
    expect(wrap).toBeTruthy();
    expect((wrap?.textContent ?? "")).toMatch(/PER 1\D?\s*COMBO/i);
  });
});
