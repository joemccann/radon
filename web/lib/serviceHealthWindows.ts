/**
 * Per-service freshness windows for the service-health staleness gate.
 *
 * The banner is degraded when an ``ok`` row's ``updated_at`` falls outside
 * its expected refresh interval — the worker is silent, not crashed. This
 * module owns the table of expectations + the pure helpers that derive
 * "fresh / stale" without touching the DB.
 *
 * Windows are tightened during regular trading hours (9:30-16:00 ET) for
 * services whose cadence is market-hours-only; off-hours quiet on those
 * services is normal so the closed-hour window is intentionally loose.
 */

export type MarketState = "open" | "extended" | "closed";

/**
 * How a writer is triggered.
 *
 *  - ``scheduled``: a daemon, systemd timer, or cron fires this
 *    automatically without user interaction. Past-window silence
 *    indicates a real problem and SHOULD fire the degraded banner.
 *  - ``on-demand``: only runs when a user visits its page or POSTs to
 *    its scan endpoint. Past-window silence just means "nobody has
 *    looked at it today" and should NOT fire the degraded banner.
 *
 * The route handler uses this to coerce past-window ``on-demand`` rows
 * into the ``dormant`` state (informational) while ``scheduled`` rows
 * continue to coerce into ``stale`` (degraded).
 */
export type ServiceCategory = "scheduled" | "on-demand";

type Window = {
  open: number;
  extended: number;
  closed: number;
  category: ServiceCategory;
  /**
   * True iff the writer's data-flow depends on IB Gateway. The
   * watchdog (scripts/watchdog/check.py) keys off this to group alerts
   * into a single "IB Gateway awaiting 2FA / unreachable" message when
   * the upstream root cause is IB rather than N independent failures.
   *
   * Verified against each writer's source code (see test_services.py),
   * not against an aspirational taxonomy. UW-only / Flex-only /
   * Playwright-only writers are FALSE even if they live on the same
   * dashboard as IB-backed services.
   */
  requires_ib: boolean;
};

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

/**
 * Per-service freshness windows in ms. Values match the spec table:
 *
 *   newsfeed-scraper     5m always
 *   orders-sync          10m open, 3d closed   (writer: scripts/ib_orders.py)
 *   portfolio-sync       10m open, 3d closed   (writer: scripts/ib_sync.py)
 *   orders-read-compare  10m open, 3d closed   (writer: web/app/api/orders/route.ts)
 *   journal-sync         10m always
 *   cash-flow-sync       25h (daily handler)
 *   fill-monitor         5m open, 1h closed
 *   exit-orders          5m open, 1h closed
 *   flex-token-check     25h (daily)
 *   cri-scan             35m open, 1d closed
 *   gex-scan             30m open, 1d closed
 *   vcg-scan             15m open, 1d closed   (5-min cadence; 3 missed cycles)
 *   cta-sync             35m open, 1d closed
 *   replica-watchdog     5m always (continuous)
 *
 * Service names MUST match the canonical writer name (no ``ib-`` prefix
 * for orders/portfolio — the writers record under ``orders-sync`` /
 * ``portfolio-sync`` directly). Mismatches silently fall through to the
 * 1h default and fire the banner overnight + on weekends.
 */
export const SERVICE_FRESHNESS_WINDOWS: Record<string, Window> = {
  "newsfeed-scraper": { open: 5 * MIN, extended: 5 * MIN, closed: 5 * MIN, category: "scheduled", requires_ib: false },

  // Market-hours-only IB feeds. The monitor daemon gates these on
  // `requires_market_hours=True`, so they only run 09:30–16:00 ET. The
  // ``extended`` window must match ``closed`` — pre-market (04:00–09:30
  // ET) and after-hours (16:00–20:00 ET) are off-cycle for these
  // writers and a tight extended window flags them as broken when
  // they're behaving as designed. ``closed`` covers the longest
  // natural gap (Fri 16:00 ET → Mon 09:30 ET ≈ 65h) so a quiet
  // weekend doesn't trip the banner.
  "orders-sync": { open: 10 * MIN, extended: 3 * DAY, closed: 3 * DAY, category: "scheduled", requires_ib: true },
  "portfolio-sync": { open: 10 * MIN, extended: 3 * DAY, closed: 3 * DAY, category: "scheduled", requires_ib: true },
  // ``orders-read-compare`` only runs when /api/orders is hit, so even
  // though the dashboard polls it every 60s the writer itself has no
  // autonomous trigger and is treated as on-demand for the banner.
  // Still goes through FastAPI /orders/refresh → IB pool, so requires_ib=true.
  "orders-read-compare": { open: 10 * MIN, extended: 3 * DAY, closed: 3 * DAY, category: "on-demand", requires_ib: true },

  // ``journal-sync`` is also gated on market hours by the daemon. The
  // previous 10-minute ``extended`` + ``closed`` windows surfaced every
  // pre-market and after-hours window as an outage. Match the IB feed
  // pattern above so the row only fires when the writer should have
  // run inside market hours but didn't.
  "journal-sync": { open: 10 * MIN, extended: 3 * DAY, closed: 3 * DAY, category: "scheduled", requires_ib: true },
  "cash-flow-sync": { open: 25 * HOUR, extended: 25 * HOUR, closed: 25 * HOUR, category: "scheduled", requires_ib: false },

  // Both ``fill-monitor`` and ``exit-orders`` only run during market
  // hours via the monitor daemon. Their 1h closed window assumed the
  // daemon fired during extended hours too, which it does not (DST fix
  // 2026-05-14 confirmed the market-hours gate). Widen ``extended`` +
  // ``closed`` to cover the worst-case weekend gap.
  "fill-monitor": { open: 5 * MIN, extended: 3 * DAY, closed: 3 * DAY, category: "scheduled", requires_ib: true },
  "exit-orders": { open: 5 * MIN, extended: 3 * DAY, closed: 3 * DAY, category: "scheduled", requires_ib: true },

  "flex-token-check": { open: 25 * HOUR, extended: 25 * HOUR, closed: 25 * HOUR, category: "scheduled", requires_ib: false },

  // ``llm-token-index`` runs once per UTC day at 06:30 via
  // radon-llm-index.timer (Hetzner). 25h window covers the normal cadence
  // plus a couple-hour drift; any longer silence indicates the timer or
  // the Artificial Analysis API is broken. Pulls AA only — no IB.
  "llm-token-index": { open: 25 * HOUR, extended: 25 * HOUR, closed: 25 * HOUR, category: "scheduled", requires_ib: false },

  // cri-scan + vcg-scan run on Mon-Fri-only systemd timers (see CLAUDE.md
  // autonomous timers table). Closed-hour window must cover the
  // Fri-end → Mon-open gap (~65h) or a quiet weekend flips the banner.
  // Surfaced 2026-05-16: both flipped stale on a Saturday with clean
  // Friday-evening finishes because the prior 1-day closed window was
  // shorter than the weekend gap.
  "cri-scan": { open: 35 * MIN, extended: 35 * MIN, closed: 3 * DAY, category: "scheduled", requires_ib: true },
  // ``gex-scan`` still flows through ``record_service_health`` only when
  // a user POSTs the scan endpoint, so it's on-demand for banner purposes.
  // Source: scripts/gex_scan.py uses UWClient only — no IB dependency.
  "gex-scan": { open: 30 * MIN, extended: 30 * MIN, closed: 1 * DAY, category: "on-demand", requires_ib: false },
  "gamma-rotation-scan": { open: 30 * MIN, extended: 30 * MIN, closed: 1 * DAY, category: "on-demand", requires_ib: false },
  // ``vcg-scan`` has an autonomous 5-min cadence during market hours
  // (radon-vcg-refresh.timer / com.radon.vcg-refresh). The 15-min open
  // window tolerates 3 missed cycles before flagging — long enough to
  // absorb transient FastAPI or IB Gateway blips, short enough to
  // surface a real outage well inside the trading day. Closed window
  // covers the weekend gap (see cri-scan note above).
  "vcg-scan": { open: 15 * MIN, extended: 15 * MIN, closed: 3 * DAY, category: "scheduled", requires_ib: true },
  // ``cta-sync`` has an autonomous Mon-Fri schedule on the VPS
  // (radon-cta-sync.timer fires 18:15, 19:00, 21:30 UTC) plus the
  // laptop launchd plist as a redundant local trigger. Stale > 25h
  // means the timer failed across both regimes; 25h tolerates a long
  // weekend (Friday 21:30 UTC → Monday 18:15 UTC ≈ 69h) plus any
  // single missed firing.
  // MenthorQ source via Playwright — no IB dependency.
  "cta-sync": { open: 25 * HOUR, extended: 25 * HOUR, closed: 72 * HOUR, category: "scheduled", requires_ib: false },

  // Market-hours-only writers: triggered by the FastAPI scan endpoints
  // during the trading day, dormant on nights and weekends. The
  // ``closed`` window has to be wide enough to bridge a full weekend
  // (Friday 16:00 ET → Monday 09:30 ET ≈ 65h) without flipping to
  // stale. Per-service intraday cadence varies but ≤30 min during
  // market hours catches genuine outages quickly.
  // scanner / discover / flow-analysis: UW-only, no IB dependency
  // (verified against scripts/scanner.py, scripts/discover.py,
  // scripts/fetch_flow.py — all import from clients.uw_client only).
  // analyst-ratings: IB-primary with UW fallback; classified false so
  // IB-down alert grouping stays accurate — the writer still records a
  // healthy ok row when IB is unreachable but UW serves the data.
  "scanner": { open: 30 * MIN, extended: 30 * MIN, closed: 3 * DAY, category: "on-demand", requires_ib: false },
  "discover": { open: 30 * MIN, extended: 30 * MIN, closed: 3 * DAY, category: "on-demand", requires_ib: false },
  "flow-analysis": { open: 30 * MIN, extended: 30 * MIN, closed: 3 * DAY, category: "on-demand", requires_ib: false },
  "analyst-ratings": { open: 30 * MIN, extended: 30 * MIN, closed: 3 * DAY, category: "on-demand", requires_ib: false },
  // ``leap-scan`` runs once daily (radon-leap.timer) and via on-demand
  // dashboard refresh. Daily cadence so 26h covers a weekend (Fri →
  // Mon morning) without flipping stale; the on-demand button can
  // bring it fresh in between.
  "leap-scan": { open: 26 * HOUR, extended: 26 * HOUR, closed: 3 * DAY, category: "scheduled", requires_ib: false },
  // ``garch-scan`` runs on-demand via the dashboard plus an optional
  // scheduled timer (not yet shipped — when it lands, default to a
  // mid-session cadence). UW-only data flow, no IB dependency.
  "garch-scan": { open: 26 * HOUR, extended: 26 * HOUR, closed: 3 * DAY, category: "scheduled", requires_ib: false },

  // ``replica-watchdog`` and ``watchdog-alerts`` are EVENT-DRIVEN
  // writers: they only record a service_health row when something
  // actually happens (a replica heal in the watchdog's case; an alert
  // fire in the alerts row's case). A healthy cycle returns early
  // without writing, so a tight 5-min window would flip them to stale
  // within minutes of the last event — even though "nothing happened"
  // is the desired healthy state. Use a 24h window so we still notice
  // when the writer process itself is down for a full day.
  "replica-watchdog": { open: 24 * HOUR, extended: 24 * HOUR, closed: 24 * HOUR, category: "scheduled", requires_ib: false },
  "watchdog-alerts": { open: 24 * HOUR, extended: 24 * HOUR, closed: 24 * HOUR, category: "scheduled", requires_ib: false },

  // ``ib-watchdog`` polls FastAPI /health every 60s and is event-driven
  // in nature — it writes service_health on every cycle so we can see
  // its heartbeat, but acts (restarts the gateway) only after 3
  // consecutive degraded readings. The 5-minute window absorbs one
  // missed cycle without flagging while still catching a dead watchdog
  // process within minutes. See `scripts/ib_watchdog.py` +
  // `docs/ib-gateway-healthcheck-hardening.md`.
  // ib-watchdog MONITORS IB but doesn't depend on IB being healthy to
  // run — suppressing it during IB outages would defeat its purpose.
  "ib-watchdog": { open: 5 * MIN, extended: 5 * MIN, closed: 5 * MIN, category: "scheduled", requires_ib: false },

  // ``ib-realtime-relay`` is an EVENT-DRIVEN writer for state edges: the
  // WS relay records error when its bounded stale-tick recovery ladder
  // escalates and ok when ticks resume. Since DUR-16 it ALSO refreshes
  // the row every 60s during RTH with last-tick detail (the freshness
  // probe reads it), but off-hours a healthy relay still writes nothing,
  // so a tight window would flip it stale overnight even though "silent"
  // is the desired healthy state — keep the 24h window so we still
  // notice a dead relay process for a day.
  // requires_ib MUST be false: the relay alert is precisely the signal
  // that the IB data plane is dead, so grouping it under the IB-outage
  // umbrella (which requires_ib=true does) would suppress the very alert
  // we want. Alert-only — the relay never restarts the Gateway itself.
  "ib-realtime-relay": { open: 24 * HOUR, extended: 24 * HOUR, closed: 24 * HOUR, category: "scheduled", requires_ib: false },

  // ``deploy`` is NOT a writer — it's the deploy MARKER row upserted by
  // radon-cloud deploy.sh after each green post-deploy gate (DUR-11). The
  // triggers from migration 0011 mirror it into service_health_events so
  // deploys show on the admin reliability history. Deploy cadence is
  // human-driven and irregular (days or weeks apart), so it must never be
  // treated as a silent daemon: on-demand category + a 365-day window keep
  // the freshness banner structurally quiet about it.
  "deploy": { open: 365 * DAY, extended: 365 * DAY, closed: 365 * DAY, category: "on-demand", requires_ib: false },

  // ``config-drift`` is the daily VPS configuration-drift audit
  // (radon-cloud scripts/drift_audit.py via radon-drift-audit.timer).
  // It diffs live system config (Caddyfile, compose, systemd units +
  // drop-ins, polkit, sudoers, /usr/local/bin/radon) against the
  // radon-cloud repo copies and heartbeats ok/error on EVERY run.
  // Daily 24/7 cadence — uniform 26h window (cadence + timer jitter);
  // weekends are normal run days so no wide closed window is needed.
  // Reads the filesystem + systemctl only — no IB dependency.
  "config-drift": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``db-backup`` is the nightly full Turso dump on the VPS (radon-cloud
  // scripts/db_backup.py via radon-db-backup.timer, 07:52 UTC, 24/7 —
  // weekends are normal run days). Heartbeats ok/error on EVERY run with
  // size + duration detail. 48h window: one missed night alerts before
  // the second dump is lost. Reads Turso + local disk only — no IB.
  "db-backup": { open: 48 * HOUR, extended: 48 * HOUR, closed: 48 * HOUR, category: "scheduled", requires_ib: false },

  // ``host-metrics`` is the minute-cadence host/process sampler on the VPS
  // (main-repo scripts/host_metrics_sampler.py via radon-cloud
  // radon-host-metrics.timer, DUR-12). Heartbeats ok/error on EVERY run,
  // 24/7 — a uniform 10-min window absorbs a few missed firings while
  // surfacing a dead sampler quickly. Reads /proc + systemctl +
  // /health/lite only — no IB dependency.
  "host-metrics": { open: 10 * MIN, extended: 10 * MIN, closed: 10 * MIN, category: "scheduled", requires_ib: false },

  // ``performance`` and ``oi-changes`` are mirror-fed scans (db.scan_mirror
  // SNAPSHOT_UPSERTS) that wrote heartbeats since DUR-01 but were never
  // registered here — they silently inherited the 1h default and would flap
  // the banner overnight. Both only run when a user hits their FastAPI scan
  // endpoint, so they're on-demand like scanner/discover. performance is
  // IB-primary with cache/UW/Yahoo fallbacks and still records ok when IB
  // is unreachable, so requires_ib=false (same rationale as analyst-ratings).
  "performance": { open: 30 * MIN, extended: 30 * MIN, closed: 3 * DAY, category: "on-demand", requires_ib: false },
  "oi-changes": { open: 30 * MIN, extended: 30 * MIN, closed: 3 * DAY, category: "on-demand", requires_ib: false },

  // ``preset-rebalance`` runs WEEKLY inside the monitor daemon (index
  // constituent refresh, Sundays). It shipped for months with no
  // service_health row at all; the DUR-14 structural heartbeat in
  // BaseHandler.run() covers it now. 8-day uniform window = weekly cadence
  // + one day of daemon-restart drift. UW/static data only — no IB.
  "preset-rebalance": { open: 8 * DAY, extended: 8 * DAY, closed: 8 * DAY, category: "scheduled", requires_ib: false },
};

const DEFAULT_WINDOW: Window = {
  open: 1 * HOUR,
  extended: 1 * HOUR,
  closed: 1 * HOUR,
  // Default to ``scheduled`` so the banner stays honest about silent
  // daemons we forgot to register — an unrecognised writer is more
  // likely a misnamed scheduled service than a brand-new on-demand
  // surface.
  category: "scheduled",
  // Default to ``false`` so a new/unknown service never gets silently
  // suppressed by IB-down alert grouping. Misclassified as needs-IB
  // would be a worse failure than an extra per-service alert.
  requires_ib: false,
};

/**
 * Resolve the freshness window for ``service`` under the given market
 * state. Unknown services fall back to a 1h default.
 */
export function getFreshnessWindowMs(service: string, market: MarketState): number {
  const entry = SERVICE_FRESHNESS_WINDOWS[service] ?? DEFAULT_WINDOW;
  return entry[market];
}

/**
 * Resolve the trigger-category for ``service``. Unknown services fall
 * back to ``scheduled`` — the safer default so the banner keeps
 * shouting about silent daemons we forgot to register here.
 */
export function getServiceCategory(service: string): ServiceCategory {
  const entry = SERVICE_FRESHNESS_WINDOWS[service] ?? DEFAULT_WINDOW;
  return entry.category;
}

/**
 * True iff ``service`` is in the IB-dependent set. Unknown services
 * return false so we never silently group/suppress alerts on a writer
 * we haven't classified yet. Mirrors ``requires_ib(service)`` in
 * scripts/watchdog/services.py; a Python<->TS contract test in
 * scripts/tests/test_watchdog/test_services.py guards drift.
 */
export function requiresIb(service: string): boolean {
  const entry = SERVICE_FRESHNESS_WINDOWS[service] ?? DEFAULT_WINDOW;
  return entry.requires_ib;
}

/**
 * True when ``updatedAt`` is past ``service``'s freshness window. Garbage
 * or missing timestamps are treated as stale — the worker hasn't proven
 * itself live, so it shouldn't be assumed live.
 */
export function isStale(
  service: string,
  updatedAt: string | null | undefined,
  market: MarketState,
  nowMs: number = Date.now(),
): boolean {
  if (!updatedAt) return true;
  const ts = Date.parse(updatedAt);
  if (Number.isNaN(ts)) return true;
  const window = getFreshnessWindowMs(service, market);
  return nowMs - ts > window;
}

/**
 * Server-side market-state derivation, mirrored from
 * web/lib/useMarketHours.ts but pure (no React, no setInterval).
 *
 * Returns the current MarketState in America/New_York. Holidays are
 * ignored — they're rare (~10/yr) and not worth a calendar dependency.
 */
export function getMarketStateFromDate(now: Date = new Date()): MarketState {
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = et.getDay(); // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return "closed";

  const minutes = et.getHours() * 60 + et.getMinutes();
  if (minutes >= 9 * 60 + 30 && minutes <= 16 * 60) return "open";
  if (
    (minutes >= 4 * 60 && minutes < 9 * 60 + 30) ||
    (minutes > 16 * 60 && minutes <= 20 * 60)
  ) {
    return "extended";
  }
  return "closed";
}

export type MarketPhase = "pre" | "open" | "after" | "closed";

/**
 * Finer-grained sibling of getMarketStateFromDate that splits "extended"
 * into "pre" (04:00-09:30 ET) and "after" (16:00-20:00 ET) so the Day P&L
 * card can name the correct session. Pure, no React, testable with a
 * pinned clock.
 */
export function getMarketPhaseFromDate(now: Date = new Date()): MarketPhase {
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = et.getDay(); // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return "closed";

  const minutes = et.getHours() * 60 + et.getMinutes();
  if (minutes >= 9 * 60 + 30 && minutes <= 16 * 60) return "open";
  if (minutes >= 4 * 60 && minutes < 9 * 60 + 30) return "pre";
  if (minutes > 16 * 60 && minutes <= 20 * 60) return "after";
  return "closed";
}
