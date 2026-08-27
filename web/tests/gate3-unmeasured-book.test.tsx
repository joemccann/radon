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

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";

import CorrelationRiskBanner from "@/components/CorrelationRiskBanner";
import { correlationRiskBanner } from "@/lib/correlationRiskBanner";
import type { RiskBudgetReport } from "@/lib/types";

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
  it("AttributionPanel passes showUnavailable to the banner", () => {
    const raw = readFileSync(
      resolve(__dirname, "..", "components/AttributionPanel.tsx"),
      "utf8",
    );
    const src = raw
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .split("\n")
      .filter((line) => !line.trim().startsWith("//"))
      .join("\n");
    const tag = src.slice(src.indexOf("<CorrelationRiskBanner"));
    expect(tag.slice(0, tag.indexOf("/>"))).toMatch(/showUnavailable/);
  });
});
