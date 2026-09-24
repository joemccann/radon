/**
 * @vitest-environment jsdom
 *
 * GS Johnstone, 24 Sep 2026, p. 2 has yields and no chart. This pins the
 * test graphic: a zero-baseline dot-and-range, printed coupons as solid
 * marks, and the CoreWeave desk phrase as a dotted estimate.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AiCreditYieldChart } from "../components/AiCreditYieldChart";
import {
  AI_CREDIT_YIELDS,
  NEAR_TEN_BID,
  valueLabel,
} from "../lib/aiCreditYields";

describe("AiCreditYieldChart", () => {
  it("plots printed yields from zero and marks the desk band as an estimate", () => {
    render(<AiCreditYieldChart />);

    const chart = screen.getByRole("img", { name: /AI credit dollar yields/i });
    expect(chart.getAttribute("data-axis-min")).toBe("0");
    expect(chart.getAttribute("data-axis-max")).toBe("12");
    expect(screen.getByText("0")).toBeTruthy();
    expect(screen.getByText("AI credit dollar yields")).toBeTruthy();
    expect(screen.queryByText(/\+25 bp/)).toBeNull();
    expect(screen.getByText(/S&P near 19x/)).toBeTruthy();

    for (const mark of AI_CREDIT_YIELDS) {
      expect(screen.getByText(mark.label)).toBeTruthy();
      expect(screen.getByText(valueLabel(mark))).toBeTruthy();
    }

    expect(valueLabel(AI_CREDIT_YIELDS[0])).toBe("8.60-9.75");
    expect(valueLabel(AI_CREDIT_YIELDS.find((mark) => mark.id === "ust2")!)).toBe("4.90");
    expect(screen.getByText("near 10%")).toBeTruthy();
    expect(NEAR_TEN_BID).toBe(10);

    const band = document.querySelector('[data-mark="coreweave"]');
    expect(band?.getAttribute("data-estimated")).toBe("true");
    const bandLine = band?.querySelector("line");
    expect(bandLine?.getAttribute("stroke-dasharray")).toBe("0 6");
    expect(bandLine?.getAttribute("stroke-linecap")).toBe("round");

    const printed = document.querySelector('[data-mark="softbank"]');
    expect(printed?.getAttribute("data-estimated")).toBe("false");
    expect(printed?.querySelector("line")?.getAttribute("stroke-dasharray")).toBeNull();
    expect(printed?.querySelectorAll("line").length).toBe(1);
    expect(printed?.querySelectorAll("circle").length).toBe(2);

    expect(document.querySelector('[data-mark="ust10"] line')).toBeNull();
    expect(document.querySelectorAll('[data-mark="ust10"] circle').length).toBe(1);
    expect(screen.getByText(/Not a printed coupon/)).toBeTruthy();
  });
});
