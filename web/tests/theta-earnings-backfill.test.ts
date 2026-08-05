import { describe, it, expect, vi } from "vitest";
import {
  resultsNeedEarningsBackfill,
  withinDteForRow,
  slimEarningsFromApi,
  mergeEarningsIntoThetaResults,
  backfillThetaEarningsPayload,
} from "@/lib/thetaEarningsBackfill";

describe("thetaEarningsBackfill", () => {
  it("detects pre-feature rows that lack the earnings key", () => {
    expect(resultsNeedEarningsBackfill([{ ticker: "HONA" }])).toBe(true);
    expect(resultsNeedEarningsBackfill([{ ticker: "HONA", earnings: null }])).toBe(false);
    expect(resultsNeedEarningsBackfill([])).toBe(false);
  });

  it("computes within_dte from days_until and structure dte", () => {
    expect(withinDteForRow(0, 16)).toBe(true);
    expect(withinDteForRow(16, 16)).toBe(true);
    expect(withinDteForRow(17, 16)).toBe(false);
    expect(withinDteForRow(-1, 16)).toBe(false);
    expect(withinDteForRow(0, null)).toBe(null);
  });

  it("slims API rows and drops missing payloads", () => {
    expect(slimEarningsFromApi({ missing: true, report_date: null }, 16)).toBeNull();
    expect(
      slimEarningsFromApi(
        {
          ticker: "HONA",
          report_date: "2026-08-05",
          report_time: "postmarket",
          days_until: 0,
          source: "company",
          expected_move_pct: 8.8,
        },
        16,
      ),
    ).toEqual({
      report_date: "2026-08-05",
      report_time: "postmarket",
      days_until: 0,
      within_dte: true,
      source: "company",
      expected_move_pct: 8.8,
    });
  });

  it("merges only rows that lack the earnings key", () => {
    const by = new Map([
      [
        "HONA",
        {
          ticker: "HONA",
          report_date: "2026-08-05",
          report_time: "postmarket",
          days_until: 0,
          source: "company",
          expected_move_pct: 8.8,
        },
      ],
    ]);
    const merged = mergeEarningsIntoThetaResults(
      [
        { ticker: "HONA", structure: { dte: 16 } },
        { ticker: "AAPL", earnings: null, structure: { dte: 23 } },
      ],
      by,
    );
    expect(merged[0].earnings).toMatchObject({
      report_date: "2026-08-05",
      within_dte: true,
    });
    expect(merged[1].earnings).toBeNull();
  });

  it("backfills payload via fetchBatch and fails open on error", async () => {
    const base = {
      scan_time: "2026-08-05T16:00:00Z",
      results: [{ ticker: "HONA", structure: { dte: 16 } }],
    };
    const ok = await backfillThetaEarningsPayload(base, async () => ({
      results: [
        {
          ticker: "HONA",
          report_date: "2026-08-05",
          report_time: "postmarket",
          days_until: 0,
          source: "company",
          expected_move_pct: 8.8,
        },
      ],
    }));
    expect((ok.results as Array<{ earnings: { report_date: string } }>)[0].earnings.report_date).toBe(
      "2026-08-05",
    );

    const fetchBoom = vi.fn(async () => {
      throw new Error("uw down");
    });
    const failed = await backfillThetaEarningsPayload(base, fetchBoom);
    expect(failed).toEqual(base);
    expect(fetchBoom).toHaveBeenCalledOnce();

    const already = {
      results: [{ ticker: "HONA", earnings: null, structure: { dte: 16 } }],
    };
    const skip = vi.fn(async () => ({ results: [] }));
    await backfillThetaEarningsPayload(already, skip);
    expect(skip).not.toHaveBeenCalled();
  });
});
