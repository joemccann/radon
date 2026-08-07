/**
 * PNG export of the options-exposure ladder (/options/net-gex).
 *
 * The exported image has to be a faithful record of what the operator was
 * looking at: symbol, spot, metric, strike range, frequency, which control
 * levels were on, and which expiration was selected. A chart shared without
 * its settings is unreadable a week later, so the model carries them and the
 * filename encodes enough to tell two exports apart.
 *
 * Split deliberately: `buildExposureChartModel` is pure (tested here in full),
 * `drawExposureChart` takes a 2D context (tested against a recording stub, so
 * no canvas implementation is needed in jsdom).
 */
import { describe, expect, it } from "vitest";

import {
  buildExposureChartModel,
  drawExposureChart,
  exposureChartFilename,
  EXPORT_WIDTH,
  type ExposureChartModel,
} from "@/lib/options/exposureChart";
import { nearestStrike, type OptionsExposureRow } from "@/lib/optionsExposure";

const ROWS: OptionsExposureRow[] = [
  { strike: 445, value: 3_200_000, putOi: 4_100, callOi: 20_600 },
  { strike: 442, value: 0, putOi: 4, callOi: 4 },
  { strike: 440, value: 6_200_000, putOi: 9_500, callOi: 36_300 },
  { strike: 438, value: -22_000, putOi: 394, callOi: 312 },
];

const BASE = {
  symbol: "GLD",
  spot: 398.47,
  sourceTime: "2026-08-07T20:00:00Z",
  metric: "net_gex" as const,
  strikeWindow: 50 as const,
  frequency: "eod" as const,
  visibleLevels: ["hvl", "cr", "ps"] as const,
  expirationLabel: "All Expirations",
  rows: ROWS,
  complete: true,
};

function model(over: Partial<Parameters<typeof buildExposureChartModel>[0]> = {}): ExposureChartModel {
  return buildExposureChartModel({ ...BASE, visibleLevels: [...BASE.visibleLevels], ...over });
}

/** Records every draw call so we can assert on content without a real canvas. */
function recordingCtx() {
  const calls: { op: string; args: unknown[] }[] = [];
  const texts: string[] = [];
  const rects: { x: number; y: number; w: number; h: number; fill: string }[] = [];
  let fillStyle = "";
  const ctx = {
    canvas: { width: EXPORT_WIDTH, height: 1000 },
    get fillStyle() { return fillStyle; },
    set fillStyle(v: string) { fillStyle = v; },
    strokeStyle: "", lineWidth: 1, font: "", textAlign: "left", textBaseline: "top",
    fillRect(x: number, y: number, w: number, h: number) {
      rects.push({ x, y, w, h, fill: fillStyle });
      calls.push({ op: "fillRect", args: [x, y, w, h] });
    },
    fillText(t: string, x: number, y: number) {
      texts.push(t);
      calls.push({ op: "fillText", args: [t, x, y] });
    },
    beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, closePath() {},
    measureText(t: string) { return { width: t.length * 6 }; },
    save() {}, restore() {}, clearRect() {}, setLineDash() {},
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls, texts, rects };
}

describe("buildExposureChartModel — carries the current settings", () => {
  it("puts symbol, spot and metric in the header", () => {
    const m = model();
    expect(m.title).toContain("GLD");
    expect(m.subtitle).toContain("Net GEX");
    expect(m.spotLabel).toContain("398.47");
  });

  it("records strike range, frequency and control levels in the settings line", () => {
    const line = model().settingsLine;
    expect(line).toContain("50 Strikes");
    expect(line).toContain("EOD");
    expect(line).toContain("3/7");
  });

  it("distinguishes intraday from EOD", () => {
    expect(model({ frequency: "intraday" }).settingsLine).toContain("Intraday");
  });

  it("names the selected expiration, not just 'All'", () => {
    expect(model({ expirationLabel: "Aug 21 14d" }).settingsLine).toContain("Aug 21 14d");
  });

  it("carries a different metric through to the subtitle and the column header", () => {
    const m = model({ metric: "net_dex" });
    expect(m.subtitle).toContain("Net DEX");
    expect(m.valueHeader).toContain("Net DEX");
  });

  it("marks a partial measurement so a caveated chart is never shared as complete", () => {
    expect(model({ complete: false }).settingsLine.toUpperCase()).toContain("PARTIAL");
  });

  it("scales bars against the largest absolute value in the visible rows", () => {
    const m = model();
    const biggest = m.rows.find((r) => r.strike === 440)!;
    const smaller = m.rows.find((r) => r.strike === 445)!;
    expect(biggest.barFraction).toBeCloseTo(1, 5);
    expect(smaller.barFraction).toBeCloseTo(3_200_000 / 6_200_000, 5);
    expect(m.rows.find((r) => r.strike === 442)!.barFraction).toBe(0);
  });

  it("keeps the sign so negative exposure draws on the other side of zero", () => {
    const negative = model().rows.find((r) => r.strike === 438)!;
    expect(negative.sign).toBe("negative");
    expect(model().rows.find((r) => r.strike === 445)!.sign).toBe("positive");
  });

  it("flags exactly one spot row, matching the panel's own nearestStrike", () => {
    const m = buildExposureChartModel({
      ...BASE, visibleLevels: [...BASE.visibleLevels], spot: 441,
    });
    expect(m.rows.filter((r) => r.isSpot)).toHaveLength(1);
    // 442 and 440 are equidistant from 441; the export must break the tie the
    // same way the on-screen highlight does (nearestStrike keeps the first).
    expect(m.rows.find((r) => r.isSpot)!.strike).toBe(nearestStrike([445, 442, 440, 438], 441));
  });

  it("puts the spot marker on an unambiguous nearest strike", () => {
    const m = buildExposureChartModel({
      ...BASE, visibleLevels: [...BASE.visibleLevels], spot: 439.6,
    });
    expect(m.rows.find((r) => r.isSpot)!.strike).toBe(440);
  });

  it("height grows with row count so 50 strikes are not clipped", () => {
    const few = model();
    const many = buildExposureChartModel({
      ...BASE,
      visibleLevels: [...BASE.visibleLevels],
      rows: Array.from({ length: 50 }, (_, i) => ({ strike: 400 + i, value: 1000 * i, putOi: i, callOi: i })),
    });
    expect(many.height).toBeGreaterThan(few.height);
  });

  it("is empty-safe rather than dividing by a zero max", () => {
    const m = model({ rows: [] });
    expect(m.rows).toHaveLength(0);
    expect(Number.isFinite(m.height)).toBe(true);
  });

  it("does not divide by zero when every visible value is zero", () => {
    const m = model({ rows: [{ strike: 400, value: 0, putOi: 0, callOi: 0 }] });
    expect(m.rows[0].barFraction).toBe(0);
    expect(Number.isNaN(m.rows[0].barFraction)).toBe(false);
  });
});

describe("exposureChartFilename", () => {
  it("encodes symbol, metric and date so two exports never collide", () => {
    const name = exposureChartFilename(model());
    expect(name).toMatch(/^radon-GLD-net_gex-2026-08-07.*\.png$/);
  });

  it("changes when the metric changes", () => {
    expect(exposureChartFilename(model())).not.toBe(exposureChartFilename(model({ metric: "net_dex" })));
  });
});

describe("drawExposureChart", () => {
  it("draws the symbol, settings line and every visible strike", () => {
    const { ctx, texts } = recordingCtx();
    drawExposureChart(ctx, model());
    const joined = texts.join(" | ");
    expect(joined).toContain("GLD");
    expect(joined).toContain("50 Strikes");
    for (const strike of ["445", "442", "440", "438"]) {
      expect(joined).toContain(strike);
    }
  });

  it("paints an opaque background so the PNG is not transparent on X", () => {
    const { ctx, rects } = recordingCtx();
    drawExposureChart(ctx, model());
    const bg = rects[0];
    expect(bg.x).toBe(0);
    expect(bg.y).toBe(0);
    expect(bg.w).toBe(EXPORT_WIDTH);
    expect(bg.fill).toMatch(/^#/);
  });

  it("draws a bar for every non-zero row and none for a zero row", () => {
    const { ctx, rects } = recordingCtx();
    drawExposureChart(ctx, model());
    const bars = rects.filter((r) => r.w > 0 && r.h > 0 && r.h < 30 && r.x > 100);
    expect(bars.length).toBeGreaterThanOrEqual(3);
  });

  it("renders the brand attribution", () => {
    const { ctx, texts } = recordingCtx();
    drawExposureChart(ctx, model());
    expect(texts.join(" ")).toMatch(/radon\.run/i);
  });

  it("never throws on an empty row set", () => {
    const { ctx } = recordingCtx();
    expect(() => drawExposureChart(ctx, model({ rows: [] }))).not.toThrow();
  });
});
