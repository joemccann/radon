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
    // MarketState=`extended`. fill-monitor, journal-sync, orders-sync,
    // portfolio-sync don't run in extended hours — the
    // monitor daemon gates them on `requires_market_hours=True`. So
    // the `extended` window must match `closed`, or the banner falsely
    // flags them every weekday morning between 04:00 and 09:30 ET.
    // Surfaced 2026-05-15 as a pre-market false-degraded banner.
    const services = [
      "orders-sync",
      "portfolio-sync",
      "journal-sync",
      "fill-monitor",
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

  it("does not false-stale RTH-only jobs at premarket or Monday open transitions", () => {
    const fridayClose = "2026-05-08T20:00:00Z";
    const mondayPremarket = Date.parse("2026-05-11T12:00:00Z");
    const mondayOpenPlusOne = Date.parse("2026-05-11T13:31:00Z");
    expect(isStale("fill-monitor", fridayClose, "extended", mondayPremarket)).toBe(false);
    expect(isStale("fill-monitor", fridayClose, "open", mondayOpenPlusOne)).toBe(false);
    expect(isStale("fill-monitor", fridayClose, "open", Date.parse("2026-05-11T13:36:00Z"))).toBe(true);
  });

  it("ib-realtime-relay is RTH-only: yesterday close is not stale at the open bell", () => {
    const yesterdayClose = "2026-08-12T20:00:00Z";
    const bell = Date.parse("2026-08-13T13:30:00Z");
    expect(isStale("ib-realtime-relay", yesterdayClose, "open", bell)).toBe(false);
    expect(isStale("ib-realtime-relay", yesterdayClose, "open", bell + 4 * 60_000)).toBe(false);
    expect(isStale("ib-realtime-relay", yesterdayClose, "open", Date.parse("2026-08-13T13:36:00Z"))).toBe(true);
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
    "theta-harvester",
    "discover",
    "flow-analysis",
    "analyst-ratings",
    "cri-scan",
    "vcg-scan",
  ])("%s: a Friday-4PM finish does not flip to stale by Sunday evening", (service) => {
    expect(isStale(service, friFinish, "closed", SUN_LATE)).toBe(false);
  });

  // scanner / discover / flow-analysis left this ≤30min group when
  // radon-flow-refresh.timer became their hourly caller (same 4d
  // window as theta-harvester). analyst-ratings stays on-demand.
  it.each([
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
    ["flex-token-check", "scheduled"],
    ["cri-scan", "scheduled"],
    ["vcg-scan", "scheduled"],
    ["replica-watchdog", "scheduled"],
    ["orders-sync", "scheduled"],
    ["portfolio-sync", "scheduled"],
    ["scanner", "scheduled"],
    // theta-harvester / strength-confirmation flipped from on-demand when
    // radon-signals-refresh.timer became their autonomous caller (R-068) —
    // same transition cta-sync made below.
    ["theta-harvester", "scheduled"],
    ["strength-confirmation", "scheduled"],
    ["discover", "scheduled"],
    ["flow-analysis", "scheduled"],
    ["analyst-ratings", "on-demand"],
    // R-422: data_refresh's 15-minute RTH driver runs it, so it is scheduled.
    ["gex-scan", "scheduled"],
    // cta-sync is scheduled by radon-cta-sync.timer on Hetzner — flipped
    // from on-demand when the autonomous timer landed.
    ["cta-sync", "scheduled"],
    ["watchdog-alerts", "scheduled"],
    ["orders-read-compare", "on-demand"],
  ])("%s is categorized as %s", (service, expected) => {
    expect(SERVICE_FRESHNESS_WINDOWS[service]?.category).toBe(expected);
  });
});

/**
 * Regression (2026-07-02): ``informed-flow`` and ``portfolio-archive``
 * both wrote service_health heartbeats but were never registered in the
 * windows table, so they silently inherited the 1h scheduled default and
 * flipped the footer to degraded whenever quiet for an hour.
 *
 *  - ``informed-flow`` (scripts/fetch_informed_flow.py) only writes when
 *    a user hits FastAPI ``GET /informed-flow/{ticker}`` — the subprocess
 *    bridge runs the script on demand. UW-only (UWClient), no IB. Same
 *    profile as its UW-scan siblings (scanner / discover):
 *    on-demand, 30m open/extended, 3d closed.
 *
 *  - ``portfolio-archive`` (scripts/archive_portfolio_snapshots.py) is the
 *    cold-archive oneshot on the VPS (radon-portfolio-archive.timer,
 *    05:40 UTC daily; uploads to Backblaze B2 then prunes Turso). Scheduled,
 *    uniform 48h window matching db-backup.
 *  - ``db-retention`` is the daily keep-latest sweep (radon-db-retention.timer,
 *    08:10 UTC).
 */
describe("unregistered-writer regression — informed-flow and portfolio-archive", () => {
  const MIN = 60_000;
  const HOUR = 60 * MIN;
  const DAY = 24 * HOUR;

  it("informed-flow is registered as on-demand", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["informed-flow"]).toBeDefined();
    expect(getServiceCategory("informed-flow")).toBe("on-demand");
  });

  it("informed-flow windows match its UW-scan siblings (30m/30m/3d)", () => {
    expect(getFreshnessWindowMs("informed-flow", "open")).toBe(30 * MIN);
    expect(getFreshnessWindowMs("informed-flow", "extended")).toBe(30 * MIN);
    expect(getFreshnessWindowMs("informed-flow", "closed")).toBe(3 * DAY);
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("informed-flow", state)).toBe(
        getFreshnessWindowMs("analyst-ratings", state),
      );
    }
  });

  it("informed-flow is UW-only — requires_ib = false", () => {
    expect(requiresIb("informed-flow")).toBe(false);
  });

  it("portfolio-archive is registered as scheduled", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["portfolio-archive"]).toBeDefined();
    expect(getServiceCategory("portfolio-archive")).toBe("scheduled");
  });

  it("portfolio-archive has a uniform 48h window matching db-backup", () => {
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("portfolio-archive", state)).toBe(48 * HOUR);
      expect(getFreshnessWindowMs("portfolio-archive", state)).toBe(
        getFreshnessWindowMs("db-backup", state),
      );
    }
  });

  it("portfolio-archive: a run 36h ago is still fresh", () => {
    const NOW = Date.parse("2026-07-28T10:00:00Z");
    const recent = new Date(NOW - 36 * HOUR).toISOString();
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(isStale("portfolio-archive", recent, state, NOW)).toBe(false);
    }
  });

  it("portfolio-archive: still fires once silence exceeds the 48h window", () => {
    const NOW = Date.parse("2026-07-28T10:00:00Z");
    const overWindow = new Date(NOW - 49 * HOUR).toISOString();
    expect(isStale("portfolio-archive", overWindow, "closed", NOW)).toBe(true);
  });

  it("portfolio-archive has no IB dependency — requires_ib = false", () => {
    expect(requiresIb("portfolio-archive")).toBe(false);
  });

  it("db-retention is registered as scheduled with a 48h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["db-retention"]).toBeDefined();
    expect(getServiceCategory("db-retention")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("db-retention", state)).toBe(48 * HOUR);
    }
    expect(requiresIb("db-retention")).toBe(false);
  });

  it("media-backup is registered as scheduled with a 48h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["media-backup"]).toBeDefined();
    expect(getServiceCategory("media-backup")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("media-backup", state)).toBe(48 * HOUR);
      expect(getFreshnessWindowMs("media-backup", state)).toBe(
        getFreshnessWindowMs("db-backup", state),
      );
    }
    expect(requiresIb("media-backup")).toBe(false);
  });

  // ``yield-curve`` — radon-yield-curve.timer fires daily 22:30 UTC every
  // calendar day (weekend/holiday runs heartbeat on unchanged data), so a
  // uniform 26h window matches its margin-debt sibling. Treasury CSV +
  // Yahoo SPX overlay only — no IB.
  it("yield-curve is registered as scheduled with a uniform 26h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["yield-curve"]).toBeDefined();
    expect(getServiceCategory("yield-curve")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("yield-curve", state)).toBe(26 * HOUR);
      expect(getFreshnessWindowMs("yield-curve", state)).toBe(
        getFreshnessWindowMs("margin-debt", state),
      );
    }
    expect(requiresIb("yield-curve")).toBe(false);
  });

  // ``div-yield`` — radon-divyield.timer fires daily 22:40 UTC every
  // calendar day (weekend/holiday runs heartbeat on unchanged data), so a
  // uniform 26h window matches its yield-curve sibling. GitHub constituents
  // CSV + Yahoo dividends + Turso y10 only, no IB.
  it("div-yield is registered as scheduled with a uniform 26h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["div-yield"]).toBeDefined();
    expect(getServiceCategory("div-yield")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("div-yield", state)).toBe(26 * HOUR);
      expect(getFreshnessWindowMs("div-yield", state)).toBe(
        getFreshnessWindowMs("yield-curve", state),
      );
    }
    expect(requiresIb("div-yield")).toBe(false);
  });

  // ``hy-ad`` — radon-hyad.timer fires Tue..Sat 11:00 UTC, the morning after
  // FINRA TRACE end-of-day finalization (T+1). Uniform 120h window covers the
  // T+1 lag plus 3-day weekends and bond-market-only holidays; older means
  // the writer is down. FINRA HTTP only — no IB.
  it("hy-ad is registered as scheduled with a uniform 120h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["hy-ad"]).toBeDefined();
    expect(getServiceCategory("hy-ad")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("hy-ad", state)).toBe(120 * HOUR);
    }
    expect(requiresIb("hy-ad")).toBe(false);
  });

  // ``hhlev``: radon-hhlev.timer fires daily 13:20 UTC every calendar day,
  // a cheap conditional check of the quarterly Fed Z.1 household leverage
  // source (weekend and unchanged-day runs still heartbeat), so a uniform
  // 26h window matches its margin-debt sibling. Quarterly data age is
  // legitimate and never conflated with writer health. FRED HTTP only, no IB.
  it("hhlev is registered as scheduled with a uniform 26h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["hhlev"]).toBeDefined();
    expect(getServiceCategory("hhlev")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("hhlev", state)).toBe(26 * HOUR);
      expect(getFreshnessWindowMs("hhlev", state)).toBe(
        getFreshnessWindowMs("margin-debt", state),
      );
    }
    expect(requiresIb("hhlev")).toBe(false);
  });

  // ``model-catalog``: radon-model-catalog.timer fires daily 03:10 UTC every
  // calendar day, refreshing the chat picker's frontier model per keyed LLM
  // provider. Provider releases follow a release cadence, not a market one,
  // so weekend runs heartbeat and a uniform 26h window applies. Provider
  // HTTP only, no IB.
  it("model-catalog is registered as scheduled with a uniform 26h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["model-catalog"]).toBeDefined();
    expect(getServiceCategory("model-catalog")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("model-catalog", state)).toBe(26 * HOUR);
    }
    expect(requiresIb("model-catalog")).toBe(false);
  });

  // ``vixts`` — radon-vixts.timer fires daily 02:45 UTC every calendar day,
  // ten minutes behind radon-vixcor so the Cboe CDN hits stay staggered
  // (weekend and holiday runs are 304 heartbeats), so a uniform 26h window
  // matches its vixcor / cor siblings. Cboe CDN CSVs only — no IB.
  it("vixts is registered as scheduled with a uniform 26h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["vixts"]).toBeDefined();
    expect(getServiceCategory("vixts")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("vixts", state)).toBe(26 * HOUR);
      expect(getFreshnessWindowMs("vixts", state)).toBe(
        getFreshnessWindowMs("vixcor", state),
      );
    }
    expect(requiresIb("vixts")).toBe(false);
  });

  // ``dispersion`` — radon-dispersion.timer fires daily 22:20 UTC every
  // calendar day (weekend and holiday runs are no-new-session heartbeats),
  // so a uniform 26h window matches its vixts sibling. IB daily bars with a
  // Yahoo rung that keeps the writer alive through an IB outage, so
  // requires_ib stays false.
  it("dispersion is registered as scheduled with a uniform 26h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["dispersion"]).toBeDefined();
    expect(getServiceCategory("dispersion")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("dispersion", state)).toBe(26 * HOUR);
      expect(getFreshnessWindowMs("dispersion", state)).toBe(
        getFreshnessWindowMs("vixts", state),
      );
    }
    expect(requiresIb("dispersion")).toBe(false);
  });

  // ``credit-spread`` — radon-credit-spread.timer fires daily 21:45 UTC
  // including weekends (heartbeat), so a uniform 26h window matches its
  // yield-curve sibling. IB-primary with UW/Yahoo fallback; Yahoo is
  // complete so requires_ib stays false.
  it("credit-spread is registered as scheduled with a uniform 26h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["credit-spread"]).toBeDefined();
    expect(getServiceCategory("credit-spread")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("credit-spread", state)).toBe(26 * HOUR);
      expect(getFreshnessWindowMs("credit-spread", state)).toBe(
        getFreshnessWindowMs("margin-debt", state),
      );
    }
    expect(requiresIb("credit-spread")).toBe(false);
  });

  // ``straddle`` — radon-straddle.timer fires daily 02:15 UTC every calendar
  // day (Cboe appends the session row ~20:00 ET; weekend runs are 304
  // heartbeats), so a uniform 26h window matches its margin-debt /
  // yield-curve daily siblings. Cboe CDN CSVs only — no IB.
  it("straddle is registered as scheduled with a uniform 26h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["straddle"]).toBeDefined();
    expect(getServiceCategory("straddle")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("straddle", state)).toBe(26 * HOUR);
      expect(getFreshnessWindowMs("straddle", state)).toBe(
        getFreshnessWindowMs("margin-debt", state),
      );
    }
    expect(requiresIb("straddle")).toBe(false);
  });

  // ``cor`` — radon-cor.timer fires daily 02:20 UTC every calendar day
  // (Cboe overwrites the COR CSVs after each session; weekend runs are 304
  // heartbeats), so a uniform 26h window matches its straddle / margin-debt
  // daily siblings. Cboe CDN CSVs only — no IB.
  it("cor is registered as scheduled with a uniform 26h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["cor"]).toBeDefined();
    expect(getServiceCategory("cor")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("cor", state)).toBe(26 * HOUR);
      expect(getFreshnessWindowMs("cor", state)).toBe(
        getFreshnessWindowMs("straddle", state),
      );
    }
    expect(requiresIb("cor")).toBe(false);
  });

  // ``vixcor`` — radon-vixcor.timer fires daily 02:35 UTC every calendar day,
  // fifteen minutes behind radon-cor so the COR3M row for the session exists
  // (weekend runs are 304 heartbeats), so a uniform 26h window matches its
  // cor parent. Cboe CDN plus Turso cor_history only — no IB.
  it("vixcor is registered as scheduled with a uniform 26h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["vixcor"]).toBeDefined();
    expect(getServiceCategory("vixcor")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("vixcor", state)).toBe(26 * HOUR);
      expect(getFreshnessWindowMs("vixcor", state)).toBe(
        getFreshnessWindowMs("cor", state),
      );
    }
    expect(requiresIb("vixcor")).toBe(false);
  });

  // ``ivrank`` — radon-ivrank.timer fires daily 22:10 UTC every calendar day
  // (weekend runs are unchanged-data heartbeats), so a uniform 26h window
  // matches its daily siblings. IB primary with a UW fallback, so the job
  // heartbeats through an IB outage: requires_ib stays false.
  it("ivrank is registered as scheduled with a uniform 26h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["ivrank"]).toBeDefined();
    expect(getServiceCategory("ivrank")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("ivrank", state)).toBe(26 * HOUR);
      expect(getFreshnessWindowMs("ivrank", state)).toBe(
        getFreshnessWindowMs("vixcor", state),
      );
    }
    expect(requiresIb("ivrank")).toBe(false);
  });

  // ``iei-hyg`` — radon-iei-hyg.timer fires daily 21:55 UTC every calendar day
  // (weekend runs are unchanged-data heartbeats), so a uniform 26h window
  // matches its daily siblings. IB → UW → Yahoo cascade, so the job
  // heartbeats through an IB outage: requires_ib stays false.
  it("iei-hyg is registered as scheduled with a uniform 26h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["iei-hyg"]).toBeDefined();
    expect(getServiceCategory("iei-hyg")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("iei-hyg", state)).toBe(26 * HOUR);
      expect(getFreshnessWindowMs("iei-hyg", state)).toBe(
        getFreshnessWindowMs("ivrank", state),
      );
    }
    expect(requiresIb("iei-hyg")).toBe(false);
  });

  // ``trin`` — radon-trin.timer samples IB every 5 minutes during RTH, so a
  // 15-minute open window tolerates 3 missed cycles (like vcg-scan); the
  // closing snapshot holds 24h off-hours. IB-only internals: requires_ib true.
  it("trin uses a 15m open window, 3d off-hours, requires_ib true", () => {
    // REL-052 / R-122: the off-hours window was 24h against a Mon-Fri-only
    // timer with Persistent=false, so the last heartbeat is Friday ~21:57
    // UTC and the row went stale from Saturday evening until Monday's first
    // fire — a guaranteed weekend page, the shape the 3-day windows on the
    // sibling RTH writers exist to prevent.
    expect(SERVICE_FRESHNESS_WINDOWS["trin"]).toBeDefined();
    expect(getServiceCategory("trin")).toBe("scheduled");
    expect(getFreshnessWindowMs("trin", "open")).toBe(15 * MIN);
    expect(getFreshnessWindowMs("trin", "extended")).toBe(3 * DAY);
    expect(getFreshnessWindowMs("trin", "closed")).toBe(3 * DAY);
    expect(requiresIb("trin")).toBe(true);
  });

  // ``skew`` publishes 5-minute RTH snapshots and retains a daily
  // finalization heartbeat off-hours. UW-only, no IB.
  it("skew open window spans two 5-minute timer cycles and a daily off-hours window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["skew"]).toBeDefined();
    expect(getServiceCategory("skew")).toBe("scheduled");
    expect(getFreshnessWindowMs("skew", "open")).toBe(10 * MIN);
    expect(getFreshnessWindowMs("skew", "extended")).toBe(26 * HOUR);
    expect(getFreshnessWindowMs("skew", "closed")).toBe(26 * HOUR);
    expect(requiresIb("skew")).toBe(false);
  });

  // ``skew2d`` — radon-skew2d.timer fires daily 21:50 UTC every calendar day
  // (weekend heartbeats), so a uniform 26h window matches margin-debt /
  // yield-curve / straddle. Derived from skew_history — no IB.
  it("skew2d is registered as scheduled with a uniform 26h window", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["skew2d"]).toBeDefined();
    expect(getServiceCategory("skew2d")).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("skew2d", state)).toBe(26 * HOUR);
      expect(getFreshnessWindowMs("skew2d", state)).toBe(
        getFreshnessWindowMs("margin-debt", state),
      );
    }
    expect(requiresIb("skew2d")).toBe(false);
  });

  // ``vol-cone`` — radon-vol-cone.timer fires daily 20:45 UTC Mon-Fri
  // (16:45 ET after the close grace). UW greeks only — no IB. The holiday
  // Monday heartbeat lands Fri 20:45 UTC + 72h + RandomizedDelaySec + run
  // time, so a 3d window sat exactly on the boundary; 4d per the
  // cash-flow-sync precedent.
  it("vol-cone is scheduled, 26h open, 4d closed/extended, requires_ib false", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["vol-cone"]).toBeDefined();
    expect(getServiceCategory("vol-cone")).toBe("scheduled");
    expect(getFreshnessWindowMs("vol-cone", "open")).toBe(26 * HOUR);
    expect(getFreshnessWindowMs("vol-cone", "extended")).toBe(4 * DAY);
    expect(getFreshnessWindowMs("vol-cone", "closed")).toBe(4 * DAY);
    expect(requiresIb("vol-cone")).toBe(false);
  });
});

/**
 * Regression (2026-08-13): five Equibles fetchers plus event-odds write
 * service_health directly and were never registered, so they inherited
 * the 1h scheduled default and flipped stale during RTH after a
 * successful daily/weekly run.
 */
describe("unregistered-writer regression — equibles + event-odds", () => {
  const HOUR = 60 * 60_000;
  const DAY = 24 * HOUR;

  it.each([
    "equibles-short-crowding",
    "equibles-filing-forensics",
  ])("%s is scheduled daily 26h, requires_ib false", (service) => {
    expect(SERVICE_FRESHNESS_WINDOWS[service]).toBeDefined();
    expect(getServiceCategory(service)).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs(service, state)).toBe(26 * HOUR);
      expect(getFreshnessWindowMs(service, state)).toBe(
        getFreshnessWindowMs("margin-debt", state),
      );
    }
    expect(requiresIb(service)).toBe(false);
  });

  it.each([
    "equibles-13f",
    "equibles-ats-venue-share",
    "equibles-cot-positioning",
  ])("%s is scheduled weekly 8d like preset-rebalance, requires_ib false", (service) => {
    expect(SERVICE_FRESHNESS_WINDOWS[service]).toBeDefined();
    expect(getServiceCategory(service)).toBe("scheduled");
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs(service, state)).toBe(8 * DAY);
      expect(getFreshnessWindowMs(service, state)).toBe(
        getFreshnessWindowMs("preset-rebalance", state),
      );
    }
    expect(requiresIb(service)).toBe(false);
  });

  it("daily equibles: a 20h-old ok row stays fresh", () => {
    const NOW = Date.parse("2026-08-13T18:00:00Z");
    const recent = new Date(NOW - 20 * HOUR).toISOString();
    expect(isStale("equibles-short-crowding", recent, "open", NOW)).toBe(false);
    expect(isStale("equibles-filing-forensics", recent, "open", NOW)).toBe(false);
  });

  it("weekly equibles: a 6-day-old ok row stays fresh", () => {
    const NOW = Date.parse("2026-08-13T18:00:00Z");
    const recent = new Date(NOW - 6 * DAY).toISOString();
    expect(isStale("equibles-13f", recent, "open", NOW)).toBe(false);
    expect(isStale("equibles-ats-venue-share", recent, "open", NOW)).toBe(false);
    expect(isStale("equibles-cot-positioning", recent, "open", NOW)).toBe(false);
  });

  it("event-odds matches catalysts (7h open, 4d closed), requires_ib false", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["event-odds"]).toBeDefined();
    expect(getServiceCategory("event-odds")).toBe("scheduled");
    expect(getFreshnessWindowMs("event-odds", "open")).toBe(7 * HOUR);
    expect(getFreshnessWindowMs("event-odds", "extended")).toBe(7 * HOUR);
    expect(getFreshnessWindowMs("event-odds", "closed")).toBe(4 * DAY);
    for (const state of ["open", "extended", "closed"] as MarketState[]) {
      expect(getFreshnessWindowMs("event-odds", state)).toBe(
        getFreshnessWindowMs("catalysts", state),
      );
    }
    expect(requiresIb("event-odds")).toBe(false);
  });
});

describe("getServiceCategory", () => {
  it("returns the configured category for a known scheduled service", () => {
    expect(getServiceCategory("newsfeed-scraper")).toBe("scheduled");
  });

  it("returns the configured category for a known on-demand service", () => {
    expect(getServiceCategory("analyst-ratings")).toBe("on-demand");
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
    ["breadth-scan", true],
    ["orders-sync", true],
    ["portfolio-sync", true],
    ["fill-monitor", true],
    ["journal-sync", true],
    ["execution-sweep", true],
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
    ["theta-harvester", false],
    ["discover", false],
    ["flow-analysis", false],
    ["ib-watchdog", false],
    ["dispersion", false],
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
      "breadth-scan",
      "orders-sync",
      "portfolio-sync",
      "fill-monitor",
      // `exit-orders` was here until R-141 — ExitOrdersHandler is no longer
      // registered by create_daemon(), so it writes no heartbeat and is in
      // neither catalog.
      "journal-sync",
      // Evening after-hours fill sweep (REL-012) — get_fills() from IB.
      "execution-sweep",
      "orders-read-compare",
      // REL-001: PositionReconcileHandler fetches IB positions every cycle.
      "position-reconcile",
      // radon-trin.timer samples NYSE A/D + up/down volume from IB every 5 min RTH.
      "trin",
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

/**
 * R-068 regression: radon-signals-refresh.timer fires the theta-harvester
 * and strength-confirmation scans autonomously (hourly, Mon-Fri
 * 09:00-16:00 ET), but both were still categorized ``on-demand`` — the
 * dormant-amber class the watchdog buckets exclude — so a dead timer froze
 * the Top-candidates panel with no page (the unit goes inactive, not
 * failed, so units.py misses it too).
 *
 * Windows follow the bpi-scan precedent for post-gap timer writers: the
 * wrapper skips outside market hours without heartbeating, so on a Monday
 * (or post-holiday) morning the newest row is legitimately Friday
 * afternoon's (~66h, ~90h after a holiday) — a uniform 4d window pages on
 * a genuinely dead timer without false-paging every Monday.
 */
describe("R-068 — signals-refresh scans are scheduled, not dormant-amber", () => {
  const DAY = 24 * 60 * 60_000;

  const HOUR = 60 * 60_000;

  it.each(["theta-harvester", "strength-confirmation"])(
    "%s is scheduled, with an open window that matches its hourly cadence",
    (service) => {
      // R-187: `open` was 4d — 96x the cadence — so a timer dead on Monday
      // morning went unreported until Thursday. The reason 4d was chosen (a
      // Friday-afternoon row must not page on Monday) is handled by the
      // RTH_ONLY open-bell grace, asserted in the very next case, so the
      // open window can now match what the writer actually does.
      const entry = SERVICE_FRESHNESS_WINDOWS[service];
      expect(entry?.category).toBe("scheduled");
      expect(entry?.open).toBe(3 * HOUR);
      expect(entry?.extended).toBe(4 * DAY);
      expect(entry?.closed).toBe(4 * DAY);
      expect(entry?.requires_ib).toBe(false);
    },
  );

  it.each(["theta-harvester", "strength-confirmation"])(
    "%s surfaces a timer dead since the open, within the session",
    (service) => {
      const MON_1400 = Date.parse("2026-05-11T18:00:00Z"); // Mon 2 PM ET
      const atTheOpen = new Date(Date.parse("2026-05-11T13:30:00Z")).toISOString();
      // 4.5h of session silence on an hourly writer: under the old 4d window
      // this read as fresh until Thursday.
      expect(isStale(service, atTheOpen, "open", MON_1400)).toBe(true);
    },
  );

  it("a Friday-afternoon row is not stale on Monday morning (no false page before the 10:00 ET write)", () => {
    const MON_0935 = Date.parse("2026-05-11T13:35:00Z"); // Mon 9:35 AM ET
    const friAfternoon = new Date(Date.parse("2026-05-08T19:00:00Z")).toISOString(); // Fri 3 PM ET
    expect(isStale("theta-harvester", friAfternoon, "open", MON_0935)).toBe(false);
  });

  it("a dead timer pages once the row ages past the 4d window", () => {
    const NOW = Date.parse("2026-05-13T18:00:00Z"); // Wed 2 PM ET
    const fiveDaysAgo = new Date(NOW - 5 * DAY).toISOString();
    expect(isStale("strength-confirmation", fiveDaysAgo, "open", NOW)).toBe(true);
  });
});

/**
 * Live vol-cone sample (2026-08-18): the EOD cone is a day stale for anyone
 * trading during the session, so radon-vol-cone-intraday.timer re-ranks a
 * live UW sample every 15m. Silence during RTH means the tab has fallen
 * back to yesterday's close without saying so.
 */
describe("vol-cone-intraday freshness window", () => {
  const MIN = 60_000;
  const DAY = 24 * 60 * 60_000;

  it("is scheduled, 45m open, 4d closed/extended, requires_ib false", () => {
    expect(SERVICE_FRESHNESS_WINDOWS["vol-cone-intraday"]).toBeDefined();
    expect(getServiceCategory("vol-cone-intraday")).toBe("scheduled");
    expect(getFreshnessWindowMs("vol-cone-intraday", "open")).toBe(45 * MIN);
    expect(getFreshnessWindowMs("vol-cone-intraday", "extended")).toBe(4 * DAY);
    expect(getFreshnessWindowMs("vol-cone-intraday", "closed")).toBe(4 * DAY);
    expect(requiresIb("vol-cone-intraday")).toBe(false);
  });
});
