// @vitest-environment jsdom
//
// R-283 (REL-098a): Gate 3 must not report "no correlated concentration" on a
// book it measured NOTHING of.
//
// The `unmeasured` level existed, but only on the branch where at least one
// cluster was found. A report with `clusters: []` and a non-empty
// `insufficient_data` fell through to `level: "none"` — headline "no
// correlated concentration" — which is the strongest possible all-clear and
// the exact case where the least is known. And the portfolio surface rendered
// no Gate-3 module at all, because `AttributionPanel` did not pass
// `showUnavailable`.

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

import CorrelationRiskBanner from "@/components/CorrelationRiskBanner";
import AttributionPanel from "@/components/AttributionPanel";
import { correlationRiskBanner } from "@/lib/correlationRiskBanner";
import type { AttributionData, RiskBudgetReport } from "@/lib/types";

const ATTRIBUTION = {
  total_trades: 1,
  closed_trades: 1,
  open_trades: 0,
  total_realized_pnl: 0,
  by_strategy: [],
  by_ticker: [],
  by_edge: [],
  by_risk: [],
  best_ticker: null,
  worst_ticker: null,
  kelly_calibration: {},
} as unknown as AttributionData;

vi.mock("@/lib/useAttribution", () => ({
  useAttribution: () => ({
    data: ATTRIBUTION,
    loading: false,
    error: null,
    refetch: () => undefined,
  }),
}));

afterEach(cleanup);

const NOTHING_MEASURED = {
  clusters: [],
  breaches: [],
  insufficient_data: ["AMD", "SMCI"],
} as unknown as RiskBudgetReport;

const NOTHING_TO_MEASURE = {
  clusters: [],
  breaches: [],
  insufficient_data: [],
} as unknown as RiskBudgetReport;

describe("an unmeasured book is not a clean book", () => {
  it("does not return the all-clear when every position lacks history", () => {
    const banner = correlationRiskBanner(NOTHING_MEASURED);
    expect(banner?.level).toBe("unmeasured");
    expect(banner?.headline).not.toMatch(/no correlated concentration/i);
  });

  it("names how many positions could not be measured", () => {
    const banner = correlationRiskBanner(NOTHING_MEASURED);
    expect(banner?.detail ?? "").toMatch(/2 positions/);
    expect(banner?.insufficientData).toEqual(["AMD", "SMCI"]);
  });

  it("still reports a genuinely clean book as none", () => {
    expect(correlationRiskBanner(NOTHING_TO_MEASURE)?.level).toBe("none");
  });

  it("renders the unmeasured state without needing showUnavailable", () => {
    const { queryByTestId } = render(<CorrelationRiskBanner report={NOTHING_MEASURED} />);
    const el = queryByTestId("correlation-risk-banner");
    expect(el).toBeTruthy();
    expect(el?.getAttribute("data-level")).toBe("unmeasured");
  });

  it("still renders nothing for a genuinely clean book", () => {
    const { queryByTestId } = render(<CorrelationRiskBanner report={NOTHING_TO_MEASURE} />);
    expect(queryByTestId("correlation-risk-banner")).toBeNull();
  });
});

describe("the portfolio surface asks for the unavailable state", () => {
  // Was: slice `<CorrelationRiskBanner ... />` out of AttributionPanel.tsx's
  // text and assert it matches /showUnavailable/. `showUnavailable={false}`
  // matches that regex and restores the original bug in full, and so does
  // hiding the whole banner behind a conditional, because the JSX TEXT is
  // unchanged. Render the panel instead.

  it("renders the Gate-3 module on a book it measured nothing of", () => {
    const { queryByTestId } = render(<AttributionPanel riskBudget={NOTHING_MEASURED} />);
    const el = queryByTestId("correlation-risk-banner");
    expect(el, "no Gate-3 module rendered on the portfolio surface").toBeTruthy();
    expect(el?.getAttribute("data-level")).toBe("unmeasured");
  });

  it("renders the unavailable state when the risk-budget report is missing", () => {
    // The ONLY branch `showUnavailable` actually gates: with no report at all
    // the banner would otherwise render nothing and Gate 3 would be silent.
    const { queryByTestId, getByTestId } = render(<AttributionPanel riskBudget={null} />);
    expect(queryByTestId("correlation-risk-banner")).toBeTruthy();
    expect(getByTestId("correlation-risk-banner").getAttribute("data-level")).toBe("info");
    expect(getByTestId("correlation-risk-banner").textContent).toContain(
      "Gate 3: correlation measurement unavailable",
    );
  });

  it("still renders no Gate-3 module for a genuinely clean book", () => {
    const { queryByTestId } = render(<AttributionPanel riskBudget={NOTHING_TO_MEASURE} />);
    expect(queryByTestId("correlation-risk-banner")).toBeNull();
  });
});
