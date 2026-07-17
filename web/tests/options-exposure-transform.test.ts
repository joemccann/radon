import { describe, expect, it } from "vitest";

import {
  DEFAULT_EXPOSURE_CONTROLS,
  EXPOSURE_LEVEL_OPTIONS,
  EXPOSURE_METRIC_OPTIONS,
  EXPOSURE_STRIKE_OPTIONS,
  aggregateOptionsExposure,
  selectStrikeWindow,
} from "@/lib/optionsExposure";
import { EXPOSURE_FIXTURE } from "./options-exposure-fixture";

describe("options exposure transforms", () => {
  it("declares the captured control contract and defaults", () => {
    expect(EXPOSURE_METRIC_OPTIONS.map((option) => option.label)).toEqual([
      "Net GEX",
      "Abs GEX",
      "Net DEX",
      "Abs DEX",
      "Open Interest",
    ]);
    expect(EXPOSURE_STRIKE_OPTIONS.map((option) => option.label)).toEqual([
      "5 Strikes +/-",
      "10 Strikes +/-",
      "20 Strikes +/-",
      "50 Strikes +/-",
      "All Strikes",
    ]);
    expect(EXPOSURE_LEVEL_OPTIONS.map((option) => option.label)).toEqual([
      "HVL",
      "CR",
      "PS",
      "CR 0DTE",
      "PS 0DTE",
      "1D Max",
      "1D Min",
    ]);
    expect(DEFAULT_EXPOSURE_CONTROLS).toEqual({
      metric: "net_gex",
      strikeWindow: 10,
      frequency: "eod",
      expirationDates: null,
      visibleLevels: new Set(EXPOSURE_LEVEL_OPTIONS.map((option) => option.key)),
    });
  });

  it("aggregates flattened cells by strike and selected expiration", () => {
    const all = aggregateOptionsExposure(EXPOSURE_FIXTURE, "net_gex", null);
    expect(all.map((row) => [row.strike, row.value, row.putOi, row.callOi])).toEqual([
      [90, 1, 35, 35],
      [95, 1, 50, 40],
      [100, -1, 35, 55],
      [105, 0, 0, 0],
      [110, 0, 0, 0],
    ]);

    const front = aggregateOptionsExposure(
      EXPOSURE_FIXTURE,
      "net_gex",
      new Set(["2026-07-17"]),
    );
    expect(front.map((row) => row.value)).toEqual([-1, -3, 5, 0, 0]);
  });

  it("uses positive absolute exposure and signed call-minus-put open interest", () => {
    expect(aggregateOptionsExposure(EXPOSURE_FIXTURE, "abs_gex", null).map((row) => row.value))
      .toEqual([3, 7, 11, 0, 0]);
    expect(aggregateOptionsExposure(EXPOSURE_FIXTURE, "open_interest", null).map((row) => row.value))
      .toEqual([0, -10, 20, 0, 0]);
  });

  it("ignores malformed flattened indexes without losing valid rows", () => {
    const malformed = structuredClone(EXPOSURE_FIXTURE);
    malformed.cells.strike_idx.push(99);
    malformed.cells.expiration_idx.push(0);
    malformed.cells.net_gex.push(999);
    malformed.cells.abs_gex.push(999);
    malformed.cells.net_dex.push(999);
    malformed.cells.abs_dex.push(999);
    malformed.cells.oi_call.push(999);
    malformed.cells.oi_put.push(999);

    expect(aggregateOptionsExposure(malformed, "net_gex", null).map((row) => row.value))
      .toEqual([1, 1, -1, 0, 0]);
  });

  it("selects N strikes on each side of the nearest spot strike and sorts descending", () => {
    const rows = Array.from({ length: 31 }, (_, index) => ({
      strike: 50 + index * 5,
      value: index,
      putOi: 0,
      callOi: 0,
    }));
    const selected = selectStrikeWindow(rows, 102, 10);
    expect(selected).toHaveLength(21);
    expect(selected[0].strike).toBe(150);
    expect(selected[10].strike).toBe(100);
    expect(selected[20].strike).toBe(50);
    expect(selectStrikeWindow(rows, 102, "all")[0].strike).toBe(200);
  });
});
