/**
 * @vitest-environment node
 *
 * Tests for the per-service freshness window helpers — keys the banner
 * uses to coerce stale ``ok`` rows into a ``stale`` state.
 */
import { describe, it, expect } from "vitest";

import {
  SERVICE_FRESHNESS_WINDOWS,
  getFreshnessWindowMs,
  getServiceCategory,
  isStale,
  requiresIb,
  type MarketState,
  type ServiceCategory,
} from "../lib/serviceHealthWindows";

describe("SERVICE_FRESHNESS_WINDOWS", () => {
  it("declares a single canonical map keyed by kebab-case service name", () => {
    expect(SERVICE_FRESHNESS_WINDOWS).toBeTypeOf("object");
    // Spot-check the names called out in the spec.
    expect(SERVICE_FRESHNESS_WINDOWS["newsfeed-scraper"]).toBeDefined();
    expect(SERVICE_FRESHNESS_WINDOWS["fill-monitor"]).toBeDefined();
    expect(SERVICE_FRESHNESS_WINDOWS["cash-flow-sync"]).toBeDefined();
    expect(SERVICE_FRESHNESS_WINDOWS["replica-watchdog"]).toBeDefined();
  });

  it("uses identical windows for market-aware services regardless of state when not market-gated", () => {
    // newsfeed-scraper has no market dependency — single window.
    const open = getFreshnessWindowMs("newsfeed-scraper", "open");
    const closed = getFreshnessWindowMs("newsfeed-scraper", "closed");
    expect(open).toBe(closed);
    expect(open).toBe(5 * 60_000); // 5 minutes
  });

  it("uses tighter windows during market hours for market-aware services", () => {
    const open = getFreshnessWindowMs("fill-monitor", "open");
    const closed = getFreshnessWindowMs("fill-monitor", "closed");
    expect(open).toBeLessThan(closed);
    expect(open).toBe(5 * 60_000); // 5 min open
    expect(closed).toBe(3 * 24 * 60 * 60_000); // 3 days closed (covers Fri close → Mon open)
  });

  it("collapses `extended` to `closed` for market-hours-only writers", () => {
    // pre-market (04:00-09:30 ET) + after-hours (16:00-20:00 ET) map to
    // MarketState=`extended`. fill-monitor, exit-orders, journal-sync,
    // orders-sync, portfolio-sync don't run in extended hours — the
    // monitor daemon gates them on `requires_market_hours=True`. So
    // the `extended` window must match `closed`, or the banner falsely
    // flags them every weekday morning between 04:00 and 09:30 ET.
    // Surfaced 2026-05-15 as a pre-market false-degraded banner.
    const services = [
      "orders-sync",
      "portfolio-sync",
      "journal-sync",
      "fill-monitor",
      "exit-orders",
      "orders-read-compare",
    ];
    for (const service of services) {
      const extended = getFreshnessWindowMs(service, "extended");
      const closed = getFreshnessWindowMs(service, "closed");
      expect(extended).toBe(closed);
    }
  });

  it("falls back to a 1h default for unknown service names", () => {
    expect(getFreshnessWindowMs("does-not-exist", "open")).toBe(60 * 60_000);
    expect(getFreshnessWindowMs("does-not-exist", "closed")).toBe(60 * 60_000);
  });
});

describe("isStale", () => {
  const NOW = Date.parse("2026-05-09T16:00:00Z");

  it("returns false when updated_at is within the window", () => {
    const updated = new Date(NOW - 60_000).toISOString(); // 1 min ago
    expect(isStale("newsfeed-scraper", updated, "open", NOW)).toBe(false);
  });

  it("returns true when updated_at is older than the window", () => {
    const updated = new Date(NOW - 10 * 60_000).toISOString(); // 10 min ago
    expect(isStale("newsfeed-scraper", updated, "open", NOW)).toBe(true);
  });

  it("respects market-aware window expansion (closed market = looser)", () => {
    const tenMinAgo = new Date(NOW - 10 * 60_000).toISOString();
    // fill-monitor: 5 min during open → STALE; 1h closed → FRESH
    expect(isStale("fill-monitor", tenMinAgo, "open", NOW)).toBe(true);
    expect(isStale("fill-monitor", tenMinAgo, "closed", NOW)).toBe(false);
  });

  it("treats null/empty/garbage updated_at as stale", () => {
    expect(isStale("fill-monitor", null, "open", NOW)).toBe(true);
    expect(isStale("fill-monitor", "", "open", NOW)).toBe(true);
    expect(isStale("fill-monitor", "not-a-date", "open", NOW)).toBe(true);
  });

  it("uses the 1h default for unknown services", () => {
    const fortyFiveMinAgo = new Date(NOW - 45 * 60_000).toISOString();
    expect(isStale("does-not-exist", fortyFiveMinAgo, "open", NOW)).toBe(false);
    const seventyMinAgo = new Date(NOW - 70 * 60_000).toISOString();
    expect(isStale("does-not-exist", seventyMinAgo, "open", NOW)).toBe(true);
  });

  it("type-checks MarketState union", () => {
    const states: MarketState[] = ["open", "extended", "closed"];
    expect(states).toHaveLength(3);
  });
});

/**
 * Regression: scanner / discover / flow-analysis / analyst-ratings are
 * market-hours-only writers — they only run during 9:30-16:00 ET on
 * weekdays. Off-hours quiet on those services is normal, so the
 * ``closed`` window must be wide enough to cover a weekend (~3 days)
 * without flipping them to ``stale`` and firing the banner.
 *
 * 2026-05-09 incident: all four flipped to stale on a Saturday, even
 * though they had a clean Friday-afternoon finish, because they were
 * not in the windows table and fell back to the 1h default.
 */
describe("market-hours-only services (weekend-aware closed window)", () => {
  // Use a Sunday-late check so a Friday-end finish exceeds 24h. The
  // earlier Saturday-noon assertion masked the 2026-05-16 cri-scan /
  // vcg-scan regression: ~20h gap fit inside their (then) 1-day
  // closed window. The real weekend gap is Fri-end → Mon-open ≈ 65h.
  const SUN_LATE = Date.parse("2026-05-10T20:00:00Z"); // Sun 4 PM ET, ~48h after FRI_4PM
  const FRI_4PM = Date.parse("2026-05-08T20:00:00Z"); // Fri 4 PM ET, last finish

  const friFinish = new Date(FRI_4PM).toISOString();

  it.each([
    "scanner",
    "discover",
    "flow-analysis",
    "analyst-ratings",
    "cri-scan",
    "vcg-scan",
  ])("%s: a Friday-4PM finish does not flip to stale by Sunday evening", (service) => {
    expect(isStale(service, friFinish, "closed", SUN_LATE)).toBe(false);
  });

  it.each([
    "scanner",
    "discover",
    "flow-analysis",
    "analyst-ratings",
  ])("%s: still fires fast during market hours (≤30 min)", (service) => {
    const NOW = Date.parse("2026-05-08T18:00:00Z"); // Fri 2 PM ET
    const sixtyMinAgo = new Date(NOW - 60 * 60_000).toISOString();
    expect(isStale(service, sixtyMinAgo, "open", NOW)).toBe(true);
  });

  it.each([
    { service: "cri-scan", openMin: 35 },
    { service: "vcg-scan", openMin: 15 },
  ])("$service: still fires inside its open-window cadence", ({ service, openMin }) => {
    const NOW = Date.parse("2026-05-08T18:00:00Z"); // Fri 2 PM ET
    const justOver = new Date(NOW - (openMin + 5) * 60_000).toISOString();
    expect(isStale(service, justOver, "open", NOW)).toBe(true);
  });
});

/**
 * Regression: the windows table keyed orders / portfolio writers as
 * ``ib-orders-sync`` / ``ib-portfolio-sync`` but the actual writers
 * (scripts/ib_orders.py, scripts/ib_sync.py) record under
 * ``orders-sync`` / ``portfolio-sync`` (no ``ib-`` prefix). The
 * mismatch silently demoted both to the 1h default and fired the
 * banner overnight + on weekends.
 *
 * Also: ``orders-read-compare`` (web/app/api/orders/route.ts) was
 * never in the table at all — same problem.
 *
 * All three are market-hours-only signals — same closed window as
 * the cri/gex/vcg/cta family.
 */
describe("DB-name aligned writers (orders-sync / portfolio-sync / orders-read-compare)", () => {
  const SAT_NOON = Date.parse("2026-05-09T16:00:00Z");
  const FRI_4PM = Date.parse("2026-05-08T20:00:00Z");
  const friFinish = new Date(FRI_4PM).toISOString();

  it.each([
    "orders-sync",
    "portfolio-sync",
    "orders-read-compare",
  ])("%s: a Friday-4PM finish does not flip to stale by Saturday noon", (service) => {
    expect(isStale(service, friFinish, "closed", SAT_NOON)).toBe(false);
  });

  it.each([
    "orders-sync",
    "portfolio-sync",
    "orders-read-compare",
  ])("%s: still fires fast during market hours", (service) => {
    const NOW = Date.parse("2026-05-08T18:00:00Z"); // Fri 2 PM ET
    const sixtyMinAgo = new Date(NOW - 60 * 60_000).toISOString();
    expect(isStale(service, sixtyMinAgo, "open", NOW)).toBe(true);
  });
});

/**
 * Regression: cash-flow-sync and llm-token-index both had uniform 25h
 * windows that fired every Saturday morning. cash-flow-sync skips
 * weekends (runs Mon-Fri at 17:00 ET only); llm-token-index has not
 * fired on weekends in practice. Both need a closed-state window that
 * covers the Fri-run → Mon-run gap (~72h) without alerting.
 *
 * Prior behavior (2026-06-13): 25h uniform window → Saturday morning alert
 * (18-24h after Fri run) with "market closed" annotation in the digest.
 */
describe("weekend false-positive regression — cash-flow-sync and llm-token-index", () => {
  // Fri 17:00 ET = Fri 22:00 UTC — last write for cash-flow-sync / llm-token-index.
  const FRI_22UTC = Date.parse("2026-05-08T22:00:00Z");
  const friFinish = new Date(FRI_22UTC).toISOString();

  it.each([
    "cash-flow-sync",
    "llm-token-index",
  ])("%s: a Friday-5PM-ET finish does not flip to stale by Saturday noon ET", (service) => {
    // Sat noon ET = Sat 16:00 UTC ≈ 18h after the Fri run. Old 25h window made this stale.
    const SAT_NOON_UTC = Date.parse("2026-05-09T16:00:00Z");
    expect(isStale(service, friFinish, "closed", SAT_NOON_UTC)).toBe(false);
  });

  it.each([
    "cash-flow-sync",
    "llm-token-index",
  ])("%s: a Friday-5PM-ET finish does not flip to stale by Sunday evening", (service) => {
    // Sun 20:00 UTC ≈ 46h after the Fri run. Old 25h window made this stale.
    const SUN_EVENING_UTC = Date.parse("2026-05-10T20:00:00Z");
    expect(isStale(service, friFinish, "closed", SUN_EVENING_UTC)).toBe(false);
  });

  it.each([
    "cash-flow-sync",
    "llm-token-index",
  ])("%s: still alerts quickly during market hours when missed", (service) => {
    // 26h ago on a Wednesday market-hours check → stale (open window = 25h).
    const WED_MARKET = Date.parse("2026-05-13T15:00:00Z"); // 11 AM ET Wed
    const twentySixHAgo = new Date(WED_MARKET - 26 * 60 * 60_000).toISOString();
    expect(isStale(service, twentySixHAgo, "open", WED_MARKET)).toBe(true);
  });
});

/**
 * Each entry in SERVICE_FRESHNESS_WINDOWS now carries a ``category``
 * field so the banner can distinguish:
 *
 *  - ``scheduled``: a daemon/timer/cron fires this without user action.
 *    Past-window silence is a real problem and SHOULD fire the banner.
 *  - ``on-demand``: only runs when a user visits its page or POSTs to
 *    its scan endpoint. Past-window silence means "you haven't looked
 *    at it today" and should NOT fire the banner.
 */
describe("SERVICE_FRESHNESS_WINDOWS — category field", () => {
  it("every entry declares a category", () => {
    for (const [service, entry] of Object.entries(SERVICE_FRESHNESS_WINDOWS)) {
      expect(
        entry.category,
        `service ${service} is missing the category field`,
      ).toBeDefined();
      expect(["scheduled", "on-demand"]).toContain(entry.category);
    }
  });

  it.each<[string, ServiceCategory]>([
    ["newsfeed-scraper", "scheduled"],
    ["journal-sync", "scheduled"],
    ["cash-flow-sync", "scheduled"],
    ["fill-monitor", "scheduled"],
    ["exit-orders", "scheduled"],
    ["flex-token-check", "scheduled"],
    ["cri-scan", "scheduled"],
    ["vcg-scan", "scheduled"],
    ["replica-watchdog", "scheduled"],
    ["orders-sync", "scheduled"],
    ["portfolio-sync", "scheduled"],
    ["scanner", "on-demand"],
    ["discover", "on-demand"],
    ["flow-analysis", "on-demand"],
    ["analyst-ratings", "on-demand"],
    ["gex-scan", "on-demand"],
    // cta-sync is scheduled by radon-cta-sync.timer on Hetzner — flipped
    // from on-demand when the autonomous timer landed.
    ["cta-sync", "scheduled"],
    ["watchdog-alerts", "scheduled"],
    ["orders-read-compare", "on-demand"],
  ])("%s is categorized as %s", (service, expected) => {
    expect(SERVICE_FRESHNESS_WINDOWS[service]?.category).toBe(expected);
  });
});

describe("getServiceCategory", () => {
  it("returns the configured category for a known scheduled service", () => {
    expect(getServiceCategory("newsfeed-scraper")).toBe("scheduled");
  });

  it("returns the configured category for a known on-demand service", () => {
    expect(getServiceCategory("scanner")).toBe("on-demand");
  });

  it("treats unknown services as scheduled (fail loud, not quiet)", () => {
    // An unrecognised writer is more likely to be a misnamed scheduled
    // service we forgot to register than a genuinely new on-demand
    // surface — defaulting to ``scheduled`` keeps the banner honest
    // about silent daemons.
    expect(getServiceCategory("brand-new-handler")).toBe("scheduled");
  });
});

/**
 * ``requires_ib`` flags which services depend on IB Gateway upstream. The
 * watchdog uses it to group alerts when IB Gateway is the root cause —
 * e.g. ``awaiting_2fa`` should fire ONE grouped Pushover alert rather
 * than N per-service alerts. UI may also key off this in future.
 *
 * Verified against writer source code (scripts/vcg_scan.py,
 * scripts/cri_scan.py, scripts/ib_orders.py, scripts/ib_sync.py,
 * scripts/monitor_daemon/handlers/{fill_monitor,exit_orders,
 * journal_sync}.py). UW-only services (gex-scan, scanner, discover,
 * flow-analysis) are FALSE regardless of how they were initially
 * categorised — the alert grouping must reflect actual data-flow
 * dependencies, not aspirational ones.
 */
describe("SERVICE_FRESHNESS_WINDOWS — requires_ib field", () => {
  it("every entry declares requires_ib explicitly", () => {
    for (const [service, entry] of Object.entries(SERVICE_FRESHNESS_WINDOWS)) {
      expect(
        entry.requires_ib,
        `service ${service} is missing the requires_ib field`,
      ).toBeDefined();
      expect(typeof entry.requires_ib).toBe("boolean");
    }
  });

  it.each<[string, boolean]>([
    ["vcg-scan", true],
    ["cri-scan", true],
    ["orders-sync", true],
    ["portfolio-sync", true],
    ["fill-monitor", true],
    ["exit-orders", true],
    ["journal-sync", true],
    ["orders-read-compare", true],
    ["newsfeed-scraper", false],
    ["replica-watchdog", false],
    ["cash-flow-sync", false],
    ["flex-token-check", false],
    ["cta-sync", false],
    ["analyst-ratings", false],
    ["watchdog-alerts", false],
    ["gex-scan", false],
    ["scanner", false],
    ["discover", false],
    ["flow-analysis", false],
    ["ib-watchdog", false],
  ])("%s requires_ib = %s", (service, expected) => {
    expect(SERVICE_FRESHNESS_WINDOWS[service]?.requires_ib).toBe(expected);
  });

  it("count of requires_ib=true matches the verified IB-dependent set", () => {
    const ibTrue = new Set(
      Object.entries(SERVICE_FRESHNESS_WINDOWS)
        .filter(([, w]) => w.requires_ib === true)
        .map(([k]) => k),
    );
    // Exactly the writers whose source code calls IB directly. Adding
    // a new IB-backed handler? Update this set + the writer audit
    // comment above.
    const expected = new Set([
      "vcg-scan",
      "cri-scan",
      "orders-sync",
      "portfolio-sync",
      "fill-monitor",
      "exit-orders",
      "journal-sync",
      "orders-read-compare",
    ]);
    expect(ibTrue).toEqual(expected);
  });
});

describe("requiresIb helper", () => {
  it("returns the configured flag for a known IB-dependent service", () => {
    expect(requiresIb("vcg-scan")).toBe(true);
    expect(requiresIb("orders-sync")).toBe(true);
  });

  it("returns the configured flag for a non-IB service", () => {
    expect(requiresIb("newsfeed-scraper")).toBe(false);
    expect(requiresIb("cta-sync")).toBe(false);
  });

  it("returns false for unknown services so grouping never silences a misnamed writer", () => {
    expect(requiresIb("brand-new-handler-not-registered")).toBe(false);
  });
});
