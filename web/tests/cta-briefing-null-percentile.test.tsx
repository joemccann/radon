// @vitest-environment jsdom
//
// R-292 (REL-100): the briefing must not turn a nulled percentile back into a
// number. `normalizeCtaPercentile(...) ?? 50` published a confident neutral
// reading of a value the reconciler had just refused to stand behind — and 50
// is not neutral in this ladder, it is the midpoint that suppresses every
// extreme label the operator reads the panel for.

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";

import CtaBriefing from "@/components/CtaBriefing";

afterEach(cleanup);

function row(over: Record<string, unknown> = {}) {
  return {
    underlying: "E-Mini S&P 500 Index",
    position_today: -2.4,
    position_yesterday: -2.2,
    position_1m_ago: 1.1,
    percentile_1m: null,
    percentile_3m: null,
    percentile_1y: null,
    z_score_3m: -2.1,
    ...over,
  };
}

function renderBriefing(main: Record<string, unknown>[]) {
  return render(
    <CtaBriefing tables={{ main, index: [], commodity: [] }} date="2026-08-27" />,
  );
}

describe("a nulled percentile does not become the 50th", () => {
  it("never renders a percentile label for a nulled row", () => {
    const { container } = renderBriefing([row()]);
    expect(container.textContent).not.toMatch(/50th/);
    expect(container.textContent).toMatch(/---/);
  });

  it("still reads the extreme off the z-score that nulled it", () => {
    // z = -2.1 is EXTREME SHORT on its own. Falling back to 50 suppressed it
    // to NEUTRAL whenever the percentile was missing.
    const { container } = renderBriefing([row()]);
    expect(container.textContent).toMatch(/EXTREME SHORT/);
  });

  it("does not claim a regime when neither percentile nor z survives", () => {
    const { container } = renderBriefing([row({ z_score_3m: Number.NaN })]);
    expect(container.textContent).not.toMatch(/EXTREME|HEAVY|NEUTRAL/);
    expect(container.textContent).toMatch(/UNKNOWN/);
  });

  it("tones the SPX pctile card as unknown, not as a confident reading", () => {
    const { container } = renderBriefing([row()]);
    const card = [...container.querySelectorAll(".cta-briefing-metric")].find((n) =>
      n.querySelector(".cta-briefing-metric-label")?.textContent?.includes("3M PCTILE"),
    );
    const value = card?.querySelector(".cta-briefing-metric-value") as HTMLElement;
    expect(value).toBeTruthy();
    // `?? 50` fell through both extremes and painted --text-primary, the same
    // colour a real mid-range reading earns.
    expect(value.style.color).toBe("var(--text-muted)");
  });

  it("leaves a real percentile reading exactly as it was", () => {
    const { container } = renderBriefing([
      row({ percentile_3m: 3, percentile_1m: 4, percentile_1y: 2 }),
    ]);
    expect(container.textContent).toMatch(/3rd/);
    expect(container.textContent).toMatch(/EXTREME SHORT/);
  });
});
