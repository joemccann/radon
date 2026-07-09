import { describe, expect, it } from "vitest";
import {
  CTA_AUM_BN,
  CTA_VOL_TARGET,
  ctaVolTargetModel,
  dayOverDayEstSellingDelta,
  estSellingBnFromRvol,
  formatCtaModelInputsStamp,
  priorSessionEstSellingBn,
} from "../lib/ctaVolTarget";

describe("ctaVolTargetModel", () => {
  it("returns null for missing or non-positive rvol", () => {
    expect(ctaVolTargetModel({ realizedVol: null })).toBeNull();
    expect(ctaVolTargetModel({ realizedVol: undefined })).toBeNull();
    expect(ctaVolTargetModel({ realizedVol: 0 })).toBeNull();
    expect(ctaVolTargetModel({ realizedVol: -1 })).toBeNull();
    expect(ctaVolTargetModel({ realizedVol: Number.NaN })).toBeNull();
  });

  it("matches cri_scan formula at rvol 15.66 → $144.6B", () => {
    // Python: round((1 - min(10/15.66, 2)) * 400, 1) == 144.6
    const m = ctaVolTargetModel({ realizedVol: 15.66 });
    expect(m).toEqual({
      realizedVol: 15.66,
      exposurePct: 63.9,
      forcedReductionPct: 36.1,
      estSellingBn: 144.6,
    });
  });

  it("is zero when rvol is at or below the 10% target", () => {
    expect(estSellingBnFromRvol(10)).toBe(0);
    expect(estSellingBnFromRvol(9.75)).toBe(0);
    expect(ctaVolTargetModel({ realizedVol: 9.75 })?.exposurePct).toBe(102.6);
  });

  it("caps exposure at 200% when rvol is very low", () => {
    const m = ctaVolTargetModel({ realizedVol: 3 });
    expect(m?.exposurePct).toBe(200);
    expect(m?.estSellingBn).toBe(0);
  });

  it("uses fixed AUM and target constants", () => {
    expect(CTA_AUM_BN).toBe(400);
    expect(CTA_VOL_TARGET).toBe(10);
    // rvol 20 → 50% exposure → 50% reduction → $200B
    expect(estSellingBnFromRvol(20)).toBe(200);
  });
});

describe("priorSessionEstSellingBn + dayOverDayEstSellingDelta", () => {
  const history = [
    { date: "2026-07-06", realized_vol: 18.23 },
    { date: "2026-07-07", realized_vol: 15.61 },
    { date: "2026-07-08", realized_vol: 15.66 },
  ];

  it("picks the latest prior session by currentDate", () => {
    // prior 2026-07-07 rvol 15.61 → est ~143.8
    const prior = priorSessionEstSellingBn(history, "2026-07-08");
    expect(prior).toBe(estSellingBnFromRvol(15.61));
    expect(prior).toBe(143.8);
  });

  it("skips same-day history rows when computing prior", () => {
    const prior = priorSessionEstSellingBn(history, "2026-07-08");
    expect(prior).not.toBe(estSellingBnFromRvol(15.66));
  });

  it("returns null when no prior session exists", () => {
    expect(priorSessionEstSellingBn([{ date: "2026-07-08", realized_vol: 15 }], "2026-07-08")).toBeNull();
    expect(priorSessionEstSellingBn([], "2026-07-08")).toBeNull();
    expect(priorSessionEstSellingBn(null, "2026-07-08")).toBeNull();
  });

  it("falls back to second-to-last when currentDate omitted", () => {
    expect(priorSessionEstSellingBn(history)).toBe(estSellingBnFromRvol(15.61));
  });

  it("computes day-over-day delta as current minus prior", () => {
    const prior = priorSessionEstSellingBn(history, "2026-07-08")!;
    const current = estSellingBnFromRvol(15.66)!;
    // 144.6 - 143.8 = 0.8
    expect(dayOverDayEstSellingDelta(current, prior)).toBe(0.8);
    expect(dayOverDayEstSellingDelta(144.5, 180.5)).toBe(-36);
    expect(dayOverDayEstSellingDelta(null, 100)).toBeNull();
    expect(dayOverDayEstSellingDelta(100, null)).toBeNull();
  });
});

describe("formatCtaModelInputsStamp", () => {
  it("formats rvol, target, aum, and optional as-of", () => {
    expect(
      formatCtaModelInputsStamp({
        realizedVol: 15.66,
        asOfLabel: "Jul 8, 4:00 PM ET",
      }),
    ).toBe("RVOL 15.66% · TARGET 10% · AUM $400B · AS OF Jul 8, 4:00 PM ET");
  });

  it("handles missing rvol", () => {
    expect(formatCtaModelInputsStamp({ realizedVol: null })).toBe(
      "RVOL --- · TARGET 10% · AUM $400B",
    );
  });
});
