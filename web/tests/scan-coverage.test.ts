import { describe, expect, it } from "vitest";

import {
  isCoverageFailedScan,
  payloadHasCandidates,
  pickUsableScanSnapshot,
} from "../lib/scanCoverage";

describe("scanCoverage", () => {
  it("treats a populated snapshot as usable", () => {
    expect(payloadHasCandidates({ candidates_found: 59, results: [] })).toBe(true);
    expect(isCoverageFailedScan({ candidates_found: 59, results: [{ ticker: "AAPL" }] })).toBe(false);
  });

  it("treats a 102-name empty snapshot with no coverage as failed", () => {
    const empty = { candidates_found: 0, theta_harvest_count: 0, results: [], tickers_scanned: 102 };
    expect(payloadHasCandidates(empty)).toBe(false);
    expect(isCoverageFailedScan(empty)).toBe(true);
  });

  it("picks the newest populated Turso row over a later empty quota clobber", () => {
    const picked = pickUsableScanSnapshot([
      {
        scan_time: "2026-08-14T17:15:18.159060+00:00",
        payload: JSON.stringify({
          scan_time: "2026-08-14T17:15:18.159060+00:00",
          candidates_found: 0,
          results: [],
          tickers_scanned: 102,
        }),
      },
      {
        scan_time: "2026-08-14T15:30:09.987292+00:00",
        payload: JSON.stringify({
          scan_time: "2026-08-14T15:30:09.987292+00:00",
          candidates_found: 59,
          results: [{ ticker: "TXN" }],
          tickers_scanned: 102,
        }),
      },
    ]);
    expect(picked?.data.results).toEqual([{ ticker: "TXN" }]);
    expect(picked?.scanTime).toBe("2026-08-14T15:30:09.987292+00:00");
  });
});
