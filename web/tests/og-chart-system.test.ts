import { expect, test } from "vitest";
import chartSystemSpec from "../lib/chart-system-spec.json";
import { OG, ogFamilyContract, ogSeriesColor } from "../lib/og-theme";

test("[og] shared OG theme mirrors the chart-system spec", () => {
  expect(OG.chart.radius).toBe(chartSystemSpec.surface.radiusPx);
  expect(OG.chart.padding).toBe(chartSystemSpec.surface.paddingPx);
  expect(OG.chart.axisFontFamily).toBe(chartSystemSpec.axis.fontFamily);
  expect(ogSeriesColor("primary")).toBe(chartSystemSpec.seriesRoles.primary.fallback);

  const family = ogFamilyContract("analytical-time-series");
  expect(family.renderer).toBe("svg");
  expect(family.requiresAxes).toBe(true);
  expect(family.rendererDescription).toBe(chartSystemSpec.sanctionedRenderers.svg);
});
