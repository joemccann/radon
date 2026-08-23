/**
 * @vitest-environment jsdom
 *
 * ATS venue-share regime (FINRA off-exchange volume via Equibles).
 *
 * Pure helpers: percent / z-score / print-size formatting, the classification
 * palette, the off-exchange vs short-volume divergence, the data-derived week
 * label (cadence copy is NEVER hardcoded), and the tally the strip reads.
 *
 * AtsVenueSharePanel: loader / empty-state gating, the summary strip, one row
 * per covered ticker, the thin-history reason, and the upstream-error notice.
 */
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  classificationColor,
  classificationLabel,
  countByClassification,
  formatGap,
  formatPrintSize,
  formatSharePct,
  formatZ,
  venueDivergence,
  weekLabel,
  type AtsVenueShareData,
  type AtsVenueShareRow,
} from "@/components/equibles-ats-venue-share/atsVenueShare";

/* ─── Pure helpers ───────────────────────────────────── */

describe("formatSharePct — one-decimal percent", () => {
  it("renders a 0-100 percent (never re-scales a fraction)", () => {
    expect(formatSharePct(41.53)).toBe("41.5%");
    expect(formatSharePct(4.2)).toBe("4.2%");
  });

  it("returns --- for null/undefined/non-finite", () => {
    expect(formatSharePct(null)).toBe("---");
    expect(formatSharePct(undefined)).toBe("---");
    expect(formatSharePct(Number.NaN)).toBe("---");
  });
});

describe("formatZ — signed two-decimal sigma", () => {
  it("signs both directions", () => {
    expect(formatZ(2.153)).toBe("+2.15");
    expect(formatZ(-1.5)).toBe("-1.50");
    expect(formatZ(0)).toBe("+0.00");
  });

  it("returns --- when the trailing window was too thin to score", () => {
    expect(formatZ(null)).toBe("---");
  });
});

describe("formatPrintSize — grouped share counts", () => {
  it("rounds to whole shares with grouping", () => {
    expect(formatPrintSize(1500)).toBe("1,500");
    expect(formatPrintSize(12405.6)).toBe("12,406");
  });

  it("returns --- for null", () => {
    expect(formatPrintSize(null)).toBe("---");
  });
});

describe("classification palette", () => {
  it("labels each verdict in upper case", () => {
    expect(classificationLabel("accumulation")).toBe("ACCUMULATION");
    expect(classificationLabel("distribution")).toBe("DISTRIBUTION");
    expect(classificationLabel("neutral")).toBe("NEUTRAL");
  });

  it("maps each verdict to a brand token", () => {
    expect(classificationColor("accumulation")).toBe("var(--positive)");
    expect(classificationColor("distribution")).toBe("var(--negative)");
    expect(classificationColor("neutral")).toBe("var(--text-muted)");
  });
});

describe("venueDivergence — ATS share minus short-volume share", () => {
  it("computes the divergence in percentage points", () => {
    expect(venueDivergence(row({ ats_share_pct: 42, short_volume_pct: 30 }))).toBeCloseTo(12, 10);
  });

  it("returns null when either leg is missing", () => {
    expect(venueDivergence(row({ short_volume_pct: null }))).toBeNull();
    expect(venueDivergence(row({ ats_share_pct: null }))).toBeNull();
  });
});

describe("formatGap — signed percentage points", () => {
  it("signs the divergence", () => {
    expect(formatGap(12)).toBe("+12.0 pp");
    expect(formatGap(-18.25)).toBe("-18.3 pp");
  });

  it("returns --- for null", () => {
    expect(formatGap(null)).toBe("---");
  });
});

describe("weekLabel — cadence copy derived from the data, never hardcoded", () => {
  it("names the FINRA week the payload actually carries", () => {
    expect(weekLabel("2026-08-03")).toBe("WEEK OF 2026-08-03");
  });

  it("returns --- when no week is present", () => {
    expect(weekLabel(null)).toBe("---");
  });
});

describe("countByClassification — the strip tally", () => {
  it("counts each verdict", () => {
    const rows = [
      row({ ticker: "A", classification: "accumulation" }),
      row({ ticker: "B", classification: "accumulation" }),
      row({ ticker: "C", classification: "distribution" }),
      row({ ticker: "D", classification: "neutral" }),
    ];
    expect(countByClassification(rows)).toEqual({
      accumulation: 2,
      distribution: 1,
      neutral: 1,
    });
  });

  it("counts an empty table as zeros", () => {
    expect(countByClassification([])).toEqual({
      accumulation: 0,
      distribution: 0,
      neutral: 0,
    });
  });
});

/* ─── AtsVenueSharePanel ─────────────────────────────── */

function row(overrides: Partial<AtsVenueShareRow> = {}): AtsVenueShareRow {
  return {
    ticker: "AAPL",
    week_start_date: "2026-08-03",
    ats_volume: 4_000_000,
    ats_trade_count: 8_000,
    non_ats_otc_volume: 6_000_000,
    non_ats_otc_trade_count: 30_000,
    total_off_exchange_volume: 10_000_000,
    ats_share_pct: 40,
    avg_ats_print_size: 500,
    avg_non_ats_print_size: 200,
    ats_share_z: 2.1,
    avg_ats_print_size_z: 1.8,
    short_volume_pct: 30,
    consolidated_volume: null,
    consolidated_volume_source: null,
    off_exchange_share_pct: null,
    classification: "accumulation",
    classification_reason: null,
    z_observations: 52,
    ...overrides,
  };
}

function data(overrides: Partial<AtsVenueShareData> = {}): AtsVenueShareData {
  const current = overrides.current ?? [row()];
  return {
    scan_time: "2026-08-11T12:00:00Z",
    source: "finra-off-exchange-weekly",
    count: current.length,
    tickers: current.map((r) => r.ticker ?? ""),
    week_start_date: "2026-08-03",
    thresholds: {
      accumulation_z: 1,
      distribution_z: -1,
      z_window_weeks: 52,
      min_history_weeks: 12,
    },
    series: {},
    errors: [],
    ...overrides,
    current,
  };
}

const mockUseAtsVenueShare = vi.fn();
vi.mock("@/components/equibles-ats-venue-share/useAtsVenueShare", () => ({
  useAtsVenueShare: () => mockUseAtsVenueShare(),
}));

import AtsVenueSharePanel from "@/components/equibles-ats-venue-share/AtsVenueSharePanel";

function mockState(overrides: Record<string, unknown> = {}) {
  mockUseAtsVenueShare.mockReturnValue({
    data: null,
    loading: false,
    syncing: false,
    error: null,
    lastSync: null,
    syncNow: () => {},
    ...overrides,
  });
}

afterEach(() => {
  cleanup();
  mockUseAtsVenueShare.mockReset();
});

describe("AtsVenueSharePanel — gating", () => {
  it("shows the loader while the first payload is in flight", () => {
    mockState({ loading: true });
    render(<AtsVenueSharePanel />);
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("shows the empty state for the missing:true contract shape", () => {
    mockState({ data: { missing: true, current: [] } });
    render(<AtsVenueSharePanel />);
    expect(screen.queryByTestId("ats-venue-share-table")).toBeNull();
    expect(screen.getByText(/no off-exchange venue data yet/i)).toBeTruthy();
  });

  it("shows the empty state when the payload covers no tickers", () => {
    mockState({ data: data({ current: [] }) });
    render(<AtsVenueSharePanel />);
    expect(screen.queryByTestId("ats-venue-share-table")).toBeNull();
  });
});

describe("AtsVenueSharePanel — table", () => {
  it("renders one row per covered ticker with its derived metrics", () => {
    mockState({
      data: data({
        current: [
          row(),
          row({
            ticker: "NVDA",
            classification: "distribution",
            ats_share_pct: 33.25,
            avg_ats_print_size: 240,
            ats_share_z: -1.4,
            avg_ats_print_size_z: -2.2,
            short_volume_pct: 51.5,
          }),
        ],
      }),
    });
    render(<AtsVenueSharePanel />);

    const aapl = within(screen.getByTestId("ats-venue-share-row-AAPL"));
    expect(aapl.getByText("40.0%")).toBeTruthy();
    expect(aapl.getByText("500")).toBeTruthy();
    expect(aapl.getByText("+2.10")).toBeTruthy();
    expect(aapl.getByText("ACCUMULATION")).toBeTruthy();

    const nvda = within(screen.getByTestId("ats-venue-share-row-NVDA"));
    expect(nvda.getByText("33.3%")).toBeTruthy();
    expect(nvda.getByText("51.5%")).toBeTruthy();
    expect(nvda.getByText("DISTRIBUTION")).toBeTruthy();
  });

  it("reorders venue-share rows when Ticker is clicked", () => {
    mockState({
      data: data({
        current: [row({ ticker: "NVDA" }), row({ ticker: "AAPL" }), row({ ticker: "MSFT" })],
      }),
    });
    render(<AtsVenueSharePanel />);
    const tickers = () =>
      screen.getAllByTestId(/^ats-venue-share-row-/).map((r) => r.getAttribute("data-testid"));
    expect(tickers()[0]).toBe("ats-venue-share-row-NVDA");
    fireEvent.click(screen.getByRole("columnheader", { name: /ticker/i }));
    expect(tickers()[0]).toBe("ats-venue-share-row-AAPL");
    expect(screen.getByRole("columnheader", { name: /ticker/i }).getAttribute("aria-sort")).toBe("ascending");
  });

  it("renders the thin-history reason instead of a fake score", () => {
    mockState({
      data: data({
        current: [
          row({
            ticker: "IONQ",
            classification: "neutral",
            classification_reason: "insufficient history: 4 of 12 weeks",
            ats_share_z: null,
            avg_ats_print_size_z: null,
          }),
        ],
      }),
    });
    render(<AtsVenueSharePanel />);

    const ionq = within(screen.getByTestId("ats-venue-share-row-IONQ"));
    expect(ionq.getAllByText("---").length).toBeGreaterThan(0);
    expect(ionq.getByTitle("insufficient history: 4 of 12 weeks")).toBeTruthy();
  });

  it("labels the week from the payload rather than asserting a refresh cadence", () => {
    mockState({ data: data() });
    render(<AtsVenueSharePanel />);
    expect(screen.getByTestId("ats-venue-share-strip-week").textContent).toContain(
      "WEEK OF 2026-08-03",
    );
  });

  it("tallies accumulation and distribution in the strip", () => {
    mockState({
      data: data({
        current: [row(), row({ ticker: "NVDA", classification: "distribution" })],
      }),
    });
    render(<AtsVenueSharePanel />);
    expect(screen.getByTestId("ats-venue-share-strip-accumulation").textContent).toContain("1");
    expect(screen.getByTestId("ats-venue-share-strip-distribution").textContent).toContain("1");
  });

  it("surfaces upstream per-ticker errors without hiding the good rows", () => {
    mockState({
      data: data({ errors: [{ ticker: "ZZZZ", error: "not_found" }] }),
    });
    render(<AtsVenueSharePanel />);
    expect(screen.getByTestId("ats-venue-share-table")).toBeTruthy();
    expect(screen.getByTestId("ats-venue-share-errors").textContent).toContain("ZZZZ");
  });
});

describe("AtsVenueSharePanel — column info bubbles", () => {
  it("gives every metric column an info bubble", () => {
    mockState({ data: data() });
    render(<AtsVenueSharePanel />);
    const labels = [
      "ATS share details",
      "Share z-score details",
      "Average ATS print details",
      "Print z-score details",
      "Short volume details",
      "Divergence details",
      "Signal details",
    ];
    for (const label of labels) {
      expect(screen.getByLabelText(label)).toBeTruthy();
    }
  });
});

describe("AtsVenueSharePanel — method footnote spacing", () => {
  it("sets the bottom copy apart from the table with a divider and readable line spacing", () => {
    mockState({
      data: data({ errors: [{ ticker: "BITF", error: "Stock 'BITF' not found." }] }),
    });
    render(<AtsVenueSharePanel />);

    const block = screen.getByTestId("ats-venue-share-footnotes");
    expect(block.style.borderTop).toContain("1px solid");
    expect(Number.parseFloat(block.style.paddingTop)).toBeGreaterThanOrEqual(10);
    expect(Number.parseFloat(block.style.marginTop)).toBeGreaterThanOrEqual(12);

    const note = screen.getByTestId("ats-venue-share-methodnote");
    expect(Number.parseFloat(note.style.lineHeight)).toBeGreaterThanOrEqual(1.5);
    const errors = screen.getByTestId("ats-venue-share-errors");
    expect(Number.parseFloat(errors.style.lineHeight)).toBeGreaterThanOrEqual(1.5);
  });
});
