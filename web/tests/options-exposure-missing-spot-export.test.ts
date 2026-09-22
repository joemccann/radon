import { expect, it } from "vitest";
import { buildExposureChartModel } from "@/lib/options/exposureChart";
import { aggregateOptionsExposure } from "@/lib/optionsExposure";
import { EXPOSURE_FIXTURE } from "./options-exposure-fixture";

it("exports unavailable spot without a false spot label, marker or range", () => {
  const model = buildExposureChartModel({
    symbol: "SNDK",
    spot: null,
    sourceTime: EXPOSURE_FIXTURE.source_time,
    metric: "net_gex",
    strikeWindow: 10,
    frequency: "eod",
    visibleLevels: [],
    expirationLabel: "All Expirations",
    rows: aggregateOptionsExposure(EXPOSURE_FIXTURE, "net_gex", null),
    complete: false,
  });
  expect(model.spotLabel).toBe("SPOT UNAVAILABLE");
  expect(model.settingsLine).toContain("All Strikes");
  expect(model.settingsLine).toContain("PARTIAL MEASUREMENT");
  expect(model.rows.every((row) => !row.isSpot)).toBe(true);
});
