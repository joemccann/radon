import { describe, expect, it } from "vitest";
import {
  isPerformanceBehindPortfolioSync,
  isPortfolioBehindCurrentEtSession,
  latestPortfolioTargetDateET,
  portfolioAsOfFromLastSync,
} from "../lib/performanceFreshness";

describe("performance freshness", () => {
  it("derives the portfolio session date from last_sync", () => {
    expect(portfolioAsOfFromLastSync("2026-03-13T21:00:00Z")).toBe("2026-03-13");
    expect(portfolioAsOfFromLastSync(null)).toBeNull();
  });

  it("interprets a naive UTC last_sync written at 21:58 ET as the same ET session", () => {
    // Hetzner (UTC) host writes datetime.now().isoformat() → 2026-05-09T01:58:36
    // That instant is 2026-05-08 21:58 ET — still the same trading session.
    // Naive-string slicing (the old behavior) would return "2026-05-09" and
    // flip the freshness banner to STALE the moment UTC midnight passes.
    expect(portfolioAsOfFromLastSync("2026-05-09T01:58:36.144211")).toBe("2026-05-08");
  });

  it("respects timezone-aware last_sync values from non-UTC hosts", () => {
    // A laptop in ET that writes a tz-aware ISO must still resolve to the
    // ET trading day it was actually produced on.
    expect(portfolioAsOfFromLastSync("2026-05-08T21:58:36-04:00")).toBe("2026-05-08");
  });

  it("does not flip a portfolio behind when naive last_sync crosses UTC midnight", () => {
    // After UTC midnight, last_sync="2026-05-09T01:58:36" is still the
    // 2026-05-08 ET session. The freshness gate must NOT mark it stale.
    expect(
      isPortfolioBehindCurrentEtSession("2026-05-09T01:58:36.144211", "2026-05-08"),
    ).toBe(false);
  });

  it("marks performance as behind when portfolio sync advances after the panel loads", () => {
    expect(isPerformanceBehindPortfolioSync(
      {
        as_of: "2026-03-12",
        last_sync: "2026-03-12T20:59:00Z",
      },
      "2026-03-13T21:01:00Z",
    )).toBe(true);
  });

  it("treats matching sync timestamps and session date as current", () => {
    expect(isPerformanceBehindPortfolioSync(
      {
        as_of: "2026-03-13",
        last_sync: "2026-03-13T21:01:00Z",
      },
      "2026-03-13T21:01:00Z",
    )).toBe(false);
  });

  it("targets the latest weekday in ET for portfolio freshness checks", () => {
    expect(latestPortfolioTargetDateET(new Date("2026-03-13T16:10:00Z"))).toBe("2026-03-13");
    expect(latestPortfolioTargetDateET(new Date("2026-03-14T16:10:00Z"))).toBe("2026-03-13");
  });

  it("never rolls the ET session date early near ET midnight (double-shift regression)", () => {
    // 2026-06-13T01:06Z = Friday 21:06 ET. The old toEtDate() re-parsed an ET
    // wall-clock string as a host-local instant, so on a PT host the second ET
    // conversion pushed this to Saturday and flagged a fresh portfolio as
    // behind-session, firing a spurious /portfolio/sync from the route.
    expect(latestPortfolioTargetDateET(new Date("2026-06-13T01:06:58Z"))).toBe("2026-06-12");
    // ET midnight boundary lands on the right side in both directions.
    expect(latestPortfolioTargetDateET(new Date("2026-06-12T03:59:00Z"))).toBe("2026-06-11");
    expect(latestPortfolioTargetDateET(new Date("2026-06-12T04:01:00Z"))).toBe("2026-06-12");
    // Saturday + Sunday in ET both walk back to Friday.
    expect(latestPortfolioTargetDateET(new Date("2026-06-14T01:00:00Z"))).toBe("2026-06-12");
    expect(latestPortfolioTargetDateET(new Date("2026-06-15T01:00:00Z"))).toBe("2026-06-12");
    // A current-instant last_sync is never behind the current session.
    const nowIso = new Date().toISOString();
    expect(isPortfolioBehindCurrentEtSession(nowIso)).toBe(false);
  });

  it("marks a portfolio snapshot as behind when it still points at a prior ET session", () => {
    expect(isPortfolioBehindCurrentEtSession("2026-03-12T13:23:21Z", "2026-03-13")).toBe(true);
    expect(isPortfolioBehindCurrentEtSession("2026-03-13T13:23:21Z", "2026-03-13")).toBe(false);
  });
});
