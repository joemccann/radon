/**
 * @vitest-environment jsdom
 *
 * 13F smart-money depth layer — pure helpers and the panel.
 *
 * The load-bearing behaviour here is HONESTY: 13F is quarterly and lands up to
 * 45 days after quarter end, so it fails Gate 2 on its own. The vintage and the
 * context-only framing must survive from the payload into the view, and the
 * freshness copy must be DERIVED from disclosure.age_days, never hardcoded.
 *
 * The read route has its own node-environment suite in
 * equibles-smart-money-13f-route.test.ts.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

const mockUseSmartMoney13F = vi.fn();
vi.mock("@/components/equibles/useSmartMoney13F", () => ({
  useSmartMoney13F: (...args: unknown[]) => mockUseSmartMoney13F(...args),
}));

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

import {
  changeTone,
  formatCount,
  formatDelta,
  formatPct,
  formatShares,
  formatUsdCompact,
  stalenessTone,
  vintageHeadline,
  type SmartMoney13FData,
} from "@/components/equibles/smartMoney13F";

/* ─── Pure helpers ───────────────────────────────────── */

describe("formatCount / formatDelta", () => {
  it("groups filer counts and signs the QoQ delta", () => {
    expect(formatCount(5240)).toBe("5,240");
    expect(formatDelta(140)).toBe("+140");
    expect(formatDelta(-12)).toBe("-12");
    expect(formatDelta(0)).toBe("0");
  });

  it("returns --- for null/undefined/non-finite", () => {
    expect(formatCount(null)).toBe("---");
    expect(formatDelta(undefined)).toBe("---");
    expect(formatCount(Number.NaN)).toBe("---");
  });
});

describe("formatUsdCompact / formatShares", () => {
  it("scales position values to the largest sensible unit", () => {
    expect(formatUsdCompact(268_000_000_000)).toBe("$268.0B");
    expect(formatUsdCompact(62_000_000_000)).toBe("$62.0B");
    expect(formatUsdCompact(240_000)).toBe("$240.0K");
    expect(formatUsdCompact(0)).toBe("$0");
  });

  it("scales share counts the same way without a currency mark", () => {
    expect(formatShares(1_300_000_000)).toBe("1.3B");
    expect(formatShares(300_000_000)).toBe("300.0M");
    expect(formatShares(1_200)).toBe("1.2K");
  });

  it("returns --- when the source omitted the field", () => {
    expect(formatUsdCompact(null)).toBe("---");
    expect(formatShares(undefined)).toBe("---");
  });
});

describe("formatPct — percents arrive 0-100, never as fractions", () => {
  it("renders the client-normalized percent directly", () => {
    // EquiblesClient rewrites ownershipPercentage 0.0879 to ownershipPct 8.79.
    expect(formatPct(8.79)).toBe("8.79%");
    expect(formatPct(61.4)).toBe("61.40%");
  });

  it("returns --- for null", () => {
    expect(formatPct(null)).toBe("---");
  });
});

describe("changeTone — quarter-over-quarter position direction", () => {
  it("tones adds positive, trims negative, holds muted", () => {
    expect(changeTone("NEW")).toBe("pos");
    expect(changeTone("ADDED")).toBe("pos");
    expect(changeTone("TRIMMED")).toBe("neg");
    expect(changeTone("EXITED")).toBe("neg");
    expect(changeTone("HELD")).toBe("mut");
    expect(changeTone(null)).toBe("mut");
  });
});

describe("stalenessTone — vintage decay, not a fault", () => {
  it("never reads as a fault: an old 13F is expected, not broken", () => {
    expect(stalenessTone("fresh")).toBe("ok");
    expect(stalenessTone("current")).toBe("mut");
    expect(stalenessTone("stale")).toBe("warn");
    expect(stalenessTone(null)).toBe("mut");
  });
});

describe("vintageHeadline — derived from the payload, never hardcoded", () => {
  it("names the quarter, the statutory deadline, and the measured age", () => {
    expect(
      vintageHeadline({
        report_date: "2026-06-30",
        quarter: "Q2 2026",
        filed_by: "2026-08-14",
        age_days: 42,
        staleness: "fresh",
        cadence: "quarterly",
        lag_days: 45,
        context_only: true,
        gate_note: "Fails Gate 2 on its own.",
      }),
    ).toBe("Q2 2026 13F - filed by 2026-08-14 - 42 days old");
  });

  it("degrades without inventing a cadence claim", () => {
    expect(vintageHeadline(null)).toBe("Vintage unknown");
  });
});

/* ─── Panel ──────────────────────────────────────────── */

import SmartMoney13FPanel from "../components/equibles/SmartMoney13FPanel";

function buildPayload(overrides: Record<string, unknown> = {}): SmartMoney13FData {
  return {
    ticker: "AAPL",
    scan_time: new Date().toISOString(),
    report_date: "2026-06-30",
    disclosure: {
      report_date: "2026-06-30",
      quarter: "Q2 2026",
      filed_by: "2026-08-14",
      age_days: 42,
      staleness: "fresh",
      cadence: "quarterly",
      lag_days: 45,
      context_only: true,
      gate_note: "Fails Gate 2 on its own.",
    },
    headline: {
      filer_count: 5240,
      filer_count_delta: 140,
      new_positions: 412,
      exited_positions: 188,
      ownership_pct: 61.4,
      top_holders: [
        {
          cik: "0000102909",
          institution: "Vanguard Group Inc",
          shares: 1_300_000_000,
          value_usd: 268_000_000_000,
          ownership_pct: 8.79,
          change_in_shares: 4_000_000,
          change_type: "ADDED",
        },
      ],
      super_investors: [
        {
          investor: "Warren Buffett",
          institution: "Berkshire Hathaway Inc",
          cik: "0001067983",
          shares: 300_000_000,
          value_usd: 62_000_000_000,
          ownership_pct: 2.03,
          change_type: "TRIMMED",
        },
      ],
    },
    holders: [],
    holder_count: 3,
    ownership_series: [],
    activity: { buyers: [], sellers: [] },
    super_investors: [],
    market_context: {
      report_date: "2026-06-30",
      most_held_rank: 2,
      most_held_filers: 5240,
      most_held_filers_delta: 140,
      in_new_positions_bucket: true,
      in_sold_out_bucket: false,
    },
    ...overrides,
  } as SmartMoney13FData;
}

function hookState(
  partial: Partial<{ data: SmartMoney13FData | null; loading: boolean; error: string | null }> = {},
) {
  return {
    data: null as SmartMoney13FData | null,
    loading: false,
    error: null as string | null,
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseSmartMoney13F.mockReturnValue(state);
  return render(<SmartMoney13FPanel ticker="AAPL" />);
}

afterEach(() => {
  cleanup();
  mockUseSmartMoney13F.mockReset();
});

describe("SmartMoney13FPanel — gating", () => {
  it("shows the loader while the first payload is in flight", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading 13F institutional positioning")).toBeTruthy();
  });

  it("shows the empty state on the settled missing case", () => {
    renderPanel(
      hookState({ data: { ticker: "AAPL", missing: true } as unknown as SmartMoney13FData }),
    );
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
  });
});

describe("SmartMoney13FPanel — vintage honesty", () => {
  it("frames the data as context, never as a trigger", () => {
    renderPanel(hookState({ data: buildPayload() }));
    const banner = screen.getByTestId("smart-money-13f-vintage");
    expect(banner.textContent).toContain("CONTEXT");
    expect(banner.textContent).not.toContain("SIGNAL");
  });

  it("derives the freshness copy from the payload age, not a hardcoded cadence", () => {
    renderPanel(hookState({ data: buildPayload() }));
    const banner = screen.getByTestId("smart-money-13f-vintage");
    expect(banner.textContent).toContain("Q2 2026");
    expect(banner.textContent).toContain("42 days old");
    expect(banner.textContent).toContain("filed by 2026-08-14");
  });

  it("surfaces the gate note so the operator sees why this cannot trigger", () => {
    renderPanel(hookState({ data: buildPayload() }));
    expect(screen.getByTestId("smart-money-13f-gate-note").textContent).toContain("Gate 2");
  });
});

describe("SmartMoney13FPanel — headline metrics", () => {
  it("renders filer count, QoQ delta, and new vs exited positions", () => {
    renderPanel(hookState({ data: buildPayload() }));
    expect(screen.getByTestId("smart-money-13f-filers").textContent).toContain("5,240");
    expect(screen.getByTestId("smart-money-13f-filer-delta").textContent).toContain("+140");
    expect(screen.getByTestId("smart-money-13f-new").textContent).toContain("412");
    expect(screen.getByTestId("smart-money-13f-exited").textContent).toContain("188");
  });

  it("lists top holders by value with their quarter-over-quarter direction", () => {
    renderPanel(hookState({ data: buildPayload() }));
    const row = screen.getByTestId("smart-money-13f-holder-0");
    expect(row.textContent).toContain("Vanguard Group Inc");
    expect(row.textContent).toContain("$268.0B");
    expect(row.textContent).toContain("ADDED");
  });

  it("names super investors holding the ticker", () => {
    renderPanel(hookState({ data: buildPayload() }));
    const row = screen.getByTestId("smart-money-13f-super-0");
    expect(row.textContent).toContain("Warren Buffett");
    expect(row.textContent).toContain("Berkshire Hathaway Inc");
  });

  it("renders --- rather than a zero when a headline metric is absent", () => {
    const data = buildPayload({
      headline: { filer_count: null, filer_count_delta: null, new_positions: null, exited_positions: null, ownership_pct: null, top_holders: [], super_investors: [] },
    });
    renderPanel(hookState({ data }));
    expect(screen.getByTestId("smart-money-13f-filers").textContent).toContain("---");
  });
});
