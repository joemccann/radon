// @vitest-environment jsdom
//
// Plate 24: a stale CTA cache must not lead with a confident verdict pill.
// The posture is dated in one sentence instead.

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";

import CtaBriefing from "@/components/CtaBriefing";

afterEach(cleanup);

const main = [{
  underlying: "E-Mini S&P 500 Index",
  position_today: -2.4,
  position_yesterday: -2.2,
  position_1m_ago: 1.1,
  percentile_1m: 10,
  percentile_3m: 13,
  percentile_1y: 20,
  z_score_3m: -1.19,
}];

describe("CtaBriefing stale posture", () => {
  it("keeps the verdict pill when the cache is current", () => {
    const { container } = render(<CtaBriefing tables={{ main, index: [], commodity: [] }} estSellingBn={0} />);
    expect(container.querySelector(".cta-briefing-regime-pill")?.textContent).toBe("HEAVY SHORT");
    expect(container.querySelector('[data-testid="cta-stale-posture"]')).toBeNull();
  });

  it("dates the posture in one sentence and drops the pill when stale", () => {
    const { container } = render(
      <CtaBriefing tables={{ main, index: [], commodity: [] }} estSellingBn={0} staleAsOf="2026-10-09" />,
    );
    expect(container.querySelector(".cta-briefing-regime-pill")).toBeNull();
    expect(container.querySelector('[data-testid="cta-stale-posture"]')?.textContent).toBe(
      "CTA equity posture as of 2026-10-09: heavy short",
    );
  });
});
