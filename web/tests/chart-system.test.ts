import { describe, expect, test } from "vitest";
import chartSystemSpec from "../lib/chart-system-spec.json";
import {
  RADON_CHART_SYSTEM,
  chartFamilyLabel,
  chartRendererLabel,
  chartSeriesColor,
  chartSeriesFallback,
  resolveChartSeriesColor,
  sanctionedRendererDescription,
} from "../lib/chartSystem";

describe("radon chart system spec", () => {
  test("exports the shared chart-system JSON as the runtime source of truth", () => {
    expect(RADON_CHART_SYSTEM).toEqual(chartSystemSpec);
  });

  test("maps semantic series roles to CSS variables with fallbacks", () => {
    expect(chartSeriesColor("primary")).toBe("var(--signal-core, #05AD98)");
    expect(chartSeriesColor("comparison")).toBe("var(--chart-series-comparison, rgba(148, 163, 184, 0.72))");
    expect(chartSeriesFallback("dislocation")).toBe("#e55fb5");
    expect(resolveChartSeriesColor("fault")).toBe("#E85D6C");
  });

  test("exposes human-readable family and renderer labels", () => {
    expect(chartFamilyLabel("analytical-time-series")).toBe("Analytical Time Series");
    expect(chartRendererLabel("live-trace")).toBe("canvas-adapter");
    expect(sanctionedRendererDescription("svg")).toContain("Default for operator charts");
  });

  test("defines four sanctioned chart families with explicit renderer guidance", () => {
    expect(Object.keys(chartSystemSpec.families)).toEqual([
      "live-trace",
      "analytical-time-series",
      "distribution-bar",
      "matrix-heatmap",
    ]);
    expect(Object.keys(chartSystemSpec.sanctionedRenderers)).toEqual([
      "canvas-adapter",
      "svg",
      "d3-svg",
      "html-css-or-svg",
    ]);
  });
});
