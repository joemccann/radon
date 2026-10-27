/**
 * @vitest-environment jsdom
 *
 * A budgeted scan can land with fewer sessions than the lookback while older
 * days backfill (2026-10-27, QQQ). The figures then cover fewer sessions than
 * "Dark Pool Lookback: 20 Trading Days" claims, so the report says so.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sessionCoverageNote } from "@/lib/flowReportCoverage";

const hookState = vi.hoisted(() => ({
  data: null as unknown,
  status: "fresh" as string,
  error: null as string | null,
  refresh: () => {},
}));

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, isTablet: false, hasMounted: true }),
}));

afterEach(() => {
  cleanup();
  vi.doUnmock("@/lib/useTickerFlowReport");
  vi.resetModules();
});

describe("the report footer carries the coverage note", () => {
  it("renders it when the scan skipped older sessions", async () => {
    Object.assign(hookState, {
      status: "fresh",
      data: {
        ticker: "QQQ",
        fetched_at: new Date().toISOString(),
        lookback_days: 20,
        sessions_covered: 15,
        history_backfill_skipped: ["2026-10-01", "2026-09-30"],
        verdict: { direction: "BULLISH", confidence: 30 },
        dark_pool: { aggregate: { num_prints: 3000 }, daily: [{ date: "2026-10-27", num_prints: 3000 }] },
        cache_meta: { last_refresh: new Date().toISOString(), age_seconds: 5, is_stale: false },
      },
    });
    vi.doMock("@/lib/useTickerFlowReport", async (importOriginal) => ({
      ...(await importOriginal<object>()),
      useTickerFlowReport: () => hookState,
    }));
    const { default: TickerFlowReport } = await import("../components/flow-analysis/TickerFlowReport");
    render(<TickerFlowReport ticker="QQQ" />);
    expect(screen.getAllByText(/Covers 15 sessions\. 2 older sessions backfill on the next scan\./).length).toBeGreaterThan(0);
  });
});

describe("sessionCoverageNote", () => {
  it("is empty for a complete window", () => {
    expect(sessionCoverageNote({ ticker: "QQQ", lookback_days: 20, sessions_covered: 21, history_backfill_skipped: [] })).toBe("");
    expect(sessionCoverageNote({ ticker: "QQQ", lookback_days: 20 })).toBe("");
  });

  it("names the covered sessions and the backfill when days were skipped", () => {
    expect(
      sessionCoverageNote({
        ticker: "QQQ",
        lookback_days: 20,
        sessions_covered: 15,
        history_backfill_skipped: ["2026-10-01", "2026-09-30", "2026-09-29", "2026-09-26", "2026-09-25", "2026-09-24"],
      }),
    ).toBe("Covers 15 sessions. 6 older sessions backfill on the next scan.");
  });

  it("uses the singular for one skipped session", () => {
    expect(
      sessionCoverageNote({ ticker: "QQQ", sessions_covered: 20, history_backfill_skipped: ["2026-09-29"] }),
    ).toBe("Covers 20 sessions. 1 older session backfills on the next scan.");
  });
});
