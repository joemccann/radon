import { describe, expect, it } from "vitest";
import {
  isFlowReportStale,
  flowReportAgeLabel,
  flowReportTimestamp,
  FLOW_REPORT_STALENESS,
} from "@/lib/flowReportStaleness";
import { useProcessTimeZone } from "./helpers/processTimeZone";

describe("flowReportTimestamp", () => {
  it("prefers fetched_at over analysis_time", () => {
    const ts = flowReportTimestamp({
      fetched_at: "2026-05-08T15:00:00Z",
      analysis_time: "2026-05-08T14:00:00Z",
    });
    expect(ts).toBe("2026-05-08T15:00:00Z");
  });

  it("falls back to cache_meta.last_refresh", () => {
    const ts = flowReportTimestamp({
      cache_meta: { last_refresh: "2026-05-08T13:00:00Z" },
    });
    expect(ts).toBe("2026-05-08T13:00:00Z");
  });

  it("returns null when nothing is set", () => {
    expect(flowReportTimestamp(null)).toBeNull();
    expect(flowReportTimestamp(undefined)).toBeNull();
    expect(flowReportTimestamp({})).toBeNull();
  });
});

describe("isFlowReportStale", () => {
  it("treats reports without a timestamp as stale", () => {
    expect(isFlowReportStale(null)).toBe(true);
    expect(isFlowReportStale({})).toBe(true);
    expect(isFlowReportStale({ fetched_at: "not-a-date" })).toBe(true);
  });

  it("market open: stale after 10 minutes", () => {
    const now = new Date("2026-05-08T15:00:00Z"); // 11:00 ET (Friday market open)
    const fresh = isFlowReportStale(
      { fetched_at: "2026-05-08T14:55:00Z" },
      now,
      true,
    );
    const stale = isFlowReportStale(
      { fetched_at: "2026-05-08T14:30:00Z" },
      now,
      true,
    );
    expect(fresh).toBe(false);
    expect(stale).toBe(true);
  });

  it("after hours: stale after 8 hours", () => {
    const now = new Date("2026-05-08T22:00:00Z"); // 18:00 ET (after close)
    const fresh = isFlowReportStale(
      { fetched_at: "2026-05-08T20:00:00Z" },
      now,
      false,
    );
    const stale = isFlowReportStale(
      { fetched_at: "2026-05-08T08:00:00Z" },
      now,
      false,
    );
    expect(fresh).toBe(false);
    expect(stale).toBe(true);
  });

  /* R-465 / REL-164: the after-hours TTL was 8h of wall clock with no check
   * that the report postdates the close. A 09:00 ET pre-market scan viewed at
   * 16:30 ET is 7.5h old, passed as fresh, and rendered the full verdict while
   * the whole session's dark-pool flow postdated it. */
  it("after hours: a pre-close report from the same session is stale inside the 8h TTL", () => {
    const now = new Date("2026-05-08T20:30:00Z"); // 16:30 ET Friday, just after close
    expect(
      isFlowReportStale({ fetched_at: "2026-05-08T13:00:00Z" }, now, false), // 09:00 ET
    ).toBe(true);
  });

  it("after hours: a post-close report from the same session is fresh", () => {
    const now = new Date("2026-05-08T22:00:00Z"); // 18:00 ET Friday
    expect(
      isFlowReportStale({ fetched_at: "2026-05-08T20:05:00Z" }, now, false), // 16:05 ET
    ).toBe(false);
  });

  it("future timestamps are not stale (clock-skew tolerant)", () => {
    const now = new Date("2026-05-08T15:00:00Z");
    const result = isFlowReportStale(
      { fetched_at: "2026-05-08T15:01:00Z" },
      now,
      true,
    );
    expect(result).toBe(false);
  });

  it("exposes TTL constants", () => {
    expect(FLOW_REPORT_STALENESS.MARKET_HOURS_TTL_MS).toBe(600_000);
    expect(FLOW_REPORT_STALENESS.AFTER_HOURS_TTL_MS).toBe(8 * 60 * 60 * 1000);
  });

  it("naive ISO from a UTC writer is treated as UTC, not local", () => {
    // Hetzner runs UTC; older snapshots wrote naive `analysis_time`.
    // Repro the wrong-day edge: 22:03 ET on 2026-05-08 is 02:03 UTC on
    // 2026-05-09. A naive ISO string of "2026-05-09T02:03:00" must NOT
    // be parsed as 02:03 in the user's local zone (which would shift it
    // by hours and mark a fresh report stale).
    const now = new Date("2026-05-09T02:05:00Z"); // 22:05 ET — 2 minutes after the naive write
    const result = isFlowReportStale(
      { analysis_time: "2026-05-09T02:03:00.123456" }, // naive ISO, no offset
      now,
      true, // pretend market open to use the strict 10m TTL
    );
    expect(result).toBe(false);
  });
});

/* ── 2026-08-28: /flow-analysis/AMZN rendered a Jun 16 report ───────────────
 *
 * The POST 502'd on a capacity shed, the route served the cache, and the hero
 * said "LAST GOOD SCAN" with no date. Nothing between the operator and a
 * STRONGLY BULLISH options bias told them the figures were ten weeks old —
 * only the ISO dates in the history table, which needs scrolling to reach.
 * A cached report has to name its own age wherever its numbers are shown.
 */
describe("flowReportAgeLabel", () => {
  it("names the report date and how old it is", () => {
    const now = new Date("2026-08-28T18:24:00Z"); // 14:24 ET
    expect(
      flowReportAgeLabel({ fetched_at: "2026-06-16T23:43:00Z" }, now),
    ).toBe("2026-06-16 · 73 days old");
  });

  it("says today for a report from the current session", () => {
    const now = new Date("2026-08-28T18:24:00Z");
    expect(
      flowReportAgeLabel({ fetched_at: "2026-08-28T13:05:00Z" }, now),
    ).toBe("2026-08-28 · today");
  });

  it("singularises a one day old report", () => {
    const now = new Date("2026-08-28T18:24:00Z");
    expect(
      flowReportAgeLabel({ fetched_at: "2026-08-27T20:00:00Z" }, now),
    ).toBe("2026-08-27 · 1 day old");
  });

  describe("under a non-ET process zone", () => {
    // 2026-08-27 21:30 ET is 2026-08-28 01:30 UTC. A UTC label would move an
    // after-hours scan to the next trading day. The suite pins TZ to ET, under
    // which a process-local date read is indistinguishable from a market-time
    // one, so this runs under UTC where the two diverge (T-319).
    const AFTER_HOURS_SCAN = "2026-08-28T01:30:00Z";
    useProcessTimeZone("UTC", { iso: AFTER_HOURS_SCAN, localHour: 1 });

    it("dates the report in market time, not process-local", () => {
      expect(new Date(AFTER_HOURS_SCAN).getDate()).toBe(28);
      const now = new Date("2026-08-28T18:24:00Z");
      expect(
        flowReportAgeLabel({ fetched_at: AFTER_HOURS_SCAN }, now),
      ).toBe("2026-08-27 · 1 day old");
    });
  });

  it("returns null when there is no usable timestamp", () => {
    expect(flowReportAgeLabel(null)).toBeNull();
    expect(flowReportAgeLabel({})).toBeNull();
    expect(flowReportAgeLabel({ fetched_at: "not-a-date" })).toBeNull();
  });
});


describe("REL-184 (R-516): a Friday EOD report stays fresh over the weekend", () => {
  const fridayEod = { fetched_at: "2026-08-28T20:30:00.000Z" }; // Fri 16:30 ET

  it("is fresh Saturday morning", () => {
    expect(
      isFlowReportStale(fridayEod, new Date("2026-08-29T14:00:00Z"), false),
    ).toBe(false);
  });

  it("is fresh Monday pre-open", () => {
    expect(
      isFlowReportStale(fridayEod, new Date("2026-08-31T12:00:00Z"), false),
    ).toBe(false);
  });

  it("goes stale once a new close exists", () => {
    // Monday 21:30 UTC is after the Monday 16:00 ET close.
    expect(
      isFlowReportStale(fridayEod, new Date("2026-08-31T21:30:00Z"), false),
    ).toBe(true);
  });
});
