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

import staticHolidays from "../../scripts/config/market_holidays.json";

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
 *   cash-flow-sync       25h open, 4d closed  (trading-day only; skips weekends)
 *   fill-monitor         5m open, 3d closed
 *   flex-token-check     25h (daily)
 *   flex-web-service     8d on-demand (1025 lockout heartbeat only)
 *   cri-scan             35m open, 1d closed
 *   gex-scan             30m open, 1d closed
 *   vcg-scan             15m open, 1d closed   (5-min cadence; 3 missed cycles)
 *   cta-sync             35m open, 1d closed
 *   replica-watchdog     24h while the replica file exists
 *
 * Service names MUST match the canonical writer name (no ``ib-`` prefix
 * for orders/portfolio — the writers record under ``orders-sync`` /
 * ``portfolio-sync`` directly). Mismatches silently fall through to the
 * 1h default and fire the banner overnight + on weekends.
 */
export const SERVICE_FRESHNESS_WINDOWS: Record<string, Window> = {
  "newsfeed-scraper": { open: 5 * MIN, extended: 5 * MIN, closed: 5 * MIN, category: "scheduled", requires_ib: false },

  // radon-nextjs-db-watchdog fires every 60s around the clock. Registered so
  // a wedged or never-firing watchdog is itself staleness-checked (REL-033).
  "nextjs-db-read": { open: 5 * MIN, extended: 5 * MIN, closed: 5 * MIN, category: "scheduled", requires_ib: false },
  // R-325: three timer-backed units wrote no service_health row and sat in
  // neither catalog. Windows mirror scripts/watchdog/services.py.
  // radon-refresh.timer fires every 15 min through RTH (Mon-Fri 13..21 UTC);
  // the job self-skips holidays and weekends, so `closed` absorbs the weekend.
  "data-refresh": { open: 35 * MIN, extended: 3 * DAY, closed: 3 * DAY, category: "scheduled", requires_ib: false },
  // radon-incident-watchdog.timer fires every 5 min around the clock.
  "incident-watchdog": { open: 15 * MIN, extended: 15 * MIN, closed: 15 * MIN, category: "scheduled", requires_ib: false },
  // radon-demo-mirror.timer fires weekdays at 21:45 UTC, once a day.
  "demo-mirror": { open: 26 * HOUR, extended: 4 * DAY, closed: 4 * DAY, category: "scheduled", requires_ib: false },

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
  // ``cash-flow-sync`` fires once per ET trading day at 17:00 ET and
  // skips weekends + US holidays. The longest legitimate quiet period
  // is Fri 17:00 ET → Mon 17:00 ET ≈ 72h. The prior 25h uniform
  // window tripped every Saturday morning. ``closed`` and ``extended``
  // are widened to 4 days to cover the weekend gap (Fri–Mon) plus one
  // holiday-drift day; ``open`` stays at 25h to catch a missed weekday
  // run quickly during trading hours.
  "cash-flow-sync": { open: 25 * HOUR, extended: 4 * DAY, closed: 4 * DAY, category: "scheduled", requires_ib: false },
  // ``execution-sweep`` fires once per ET trading day at 20:30 ET (the
  // REL-012 evening after-hours fill sweep inside the monitor daemon)
  // and skips weekends + US holidays. The longest legitimate quiet
  // period is Fri 20:30 ET → Mon 20:30 ET ≈ 72h, so ``closed`` and
  // ``extended`` are 4 days like cash-flow-sync; ``open`` is 26h to
  // catch a missed weekday run. Pulls get_fills() from IB Gateway.
  "execution-sweep": { open: 26 * HOUR, extended: 4 * DAY, closed: 4 * DAY, category: "scheduled", requires_ib: true },

  // ``fill-monitor`` only runs during market hours via the monitor
  // daemon. Its 1h closed window assumed the daemon fired during
  // extended hours too, which it does not (DST fix 2026-05-14 confirmed
  // the market-hours gate). Widen ``extended`` + ``closed`` to cover the
  // worst-case weekend gap.
  "fill-monitor": { open: 5 * MIN, extended: 3 * DAY, closed: 3 * DAY, category: "scheduled", requires_ib: true },
  // position-reconcile — 30-min RTH IB-vs-snapshot position drift check
  // (monitor_daemon PositionReconcileHandler, REL-001). 45min open = one
  // missed cycle + slack; closed folds the weekend like its siblings.
  "position-reconcile": { open: 45 * MIN, extended: 3 * DAY, closed: 3 * DAY, category: "scheduled", requires_ib: true },

  // R-159: radon-perf-twr wrote no health row and was in neither catalog.
  // The `performance` key below is a DIFFERENT, on-demand writer
  // (portfolio_performance.py). Tue..Sat 07:30 ET; 26h open catches a missed
  // weekday run, 4d closed covers the Sat->Tue gap.
  "perf-twr": { open: 26 * HOUR, extended: 4 * DAY, closed: 4 * DAY, category: "scheduled", requires_ib: false },
  // radon-flex-pull.timer Tue..Sat 07:30/08:30 ET. Same gap as perf-twr.
  "flex-pull": { open: 26 * HOUR, extended: 4 * DAY, closed: 4 * DAY, category: "scheduled", requires_ib: false },
  "flex-token-check": { open: 25 * HOUR, extended: 25 * HOUR, closed: 25 * HOUR, category: "scheduled", requires_ib: false },
  // Token-wide Flex 1025 lockout sidecar (scripts/utils/flex_embargo.py).
  // Written only on lockout as error + next_attempt_at (7 days). Not a
  // timer: silence means the token is not locked. On-demand so a missing
  // row does not flap the banner; 8d covers the lockout plus slack.
  "flex-web-service": { open: 8 * DAY, extended: 8 * DAY, closed: 8 * DAY, category: "on-demand", requires_ib: false },
  // MenthorQ dashboard session powering /options/net-gex. Daily check;
  // its jar died silently for 11 days before this row existed (2026-08-07).
  "menthorq-session": { open: 25 * HOUR, extended: 25 * HOUR, closed: 25 * HOUR, category: "scheduled", requires_ib: false },
  // Daily LIVE probe of the MenthorQ credential re-login chain via a real
  // /options/exposure fetch — the metadata check above cannot see a broken
  // chain (2026-08-07: WAF-blocked re-login went unnoticed for 11 days).
  "menthorq-login-probe": { open: 25 * HOUR, extended: 25 * HOUR, closed: 25 * HOUR, category: "scheduled", requires_ib: false },

  // ``llm-token-index`` fires once per UTC day at 06:30 via
  // radon-llm-index.timer (Hetzner). The timer is scheduled daily but
  // has not fired on weekends in practice, giving a Fri-06:30-UTC →
  // Mon-06:30-UTC gap of ~72h. The prior 25h uniform window tripped
  // every Saturday morning. ``closed`` and ``extended`` are widened to
  // 4 days to cover the weekend gap; ``open`` stays at 25h to catch a
  // missed weekday run quickly during trading hours. Pulls AA only — no IB.
  "llm-token-index": { open: 25 * HOUR, extended: 4 * DAY, closed: 4 * DAY, category: "scheduled", requires_ib: false },

  // ``margin-debt`` — run_margin_debt_refresh.sh fires daily every calendar
  // day (FINRA monthly source; conditional GET no-ops unchanged days but the
  // snapshot + heartbeat still write). Uniform 26h window: no weekend gap.
  "margin-debt": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``yield-curve`` — radon-yield-curve.timer fires daily 22:30 UTC every calendar day (weekend runs heartbeat), so a uniform 26h window fits; Treasury CSV + Yahoo only — no IB.
  "yield-curve": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``div-yield`` — radon-divyield.timer fires daily 22:40 UTC every calendar day (weekend runs are unchanged-day heartbeats), so a uniform 26h window matches its yield-curve sibling; GitHub constituents + Yahoo dividends + Turso y10, no IB.
  "div-yield": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``credit-spread`` — radon-credit-spread.timer fires daily 21:45 UTC every
  // calendar day (weekend runs heartbeat), so a uniform 26h window fits;
  // IB HYG + SPX first, then UW, then Yahoo. Yahoo is a complete fallback
  // so this is not grouped with IB outages (requires_ib: false).
  "credit-spread": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``straddle`` — radon-straddle.timer fires daily 02:15 UTC every calendar
  // day (Cboe appends the session row ~20:00 ET; weekend runs are 304
  // heartbeats), so a uniform 26h window fits like margin-debt / yield-curve.
  // Cboe CDN CSVs only — no IB.
  "straddle": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``cor`` — radon-cor.timer fires daily 02:20 UTC every calendar day
  // (Cboe overwrites the COR CSVs after each session; weekend runs are 304
  // heartbeats), so a uniform 26h window fits like its straddle sibling.
  // Cboe CDN CSVs only — no IB.
  "cor": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``vixcor`` — radon-vixcor.timer fires daily 02:35 UTC every calendar day,
  // 15 minutes behind radon-cor (weekend and holiday runs are 304 heartbeats),
  // so a uniform 26h window matches its cor / straddle siblings. Cboe CDN plus
  // Turso cor_history only — no IB.
  "vixcor": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``ivrank`` — radon-ivrank.timer fires daily 22:10 UTC every calendar day
  // (weekend runs are unchanged-data heartbeats), so a uniform 26h window
  // matches its daily siblings. IB primary with a UW fallback, so the job
  // heartbeats through an IB outage: requires_ib stays false.
  "ivrank": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``iei-hyg`` — radon-iei-hyg.timer fires daily 21:55 UTC; IB → UW → Yahoo cascade, so requires_ib stays false.
  "iei-hyg": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``hy-ad``: radon-hyad.timer fires Tue..Sat 11:00 UTC, the morning after
  // FINRA TRACE end-of-day finalization (T+1). A uniform 120h window covers
  // the T+1 lag plus 3-day weekends and bond-market-only holidays; older
  // means the writer is down. FINRA HTTP only, no IB.
  "hy-ad": { open: 120 * HOUR, extended: 120 * HOUR, closed: 120 * HOUR, category: "scheduled", requires_ib: false },

  // ``hhlev``: radon-hhlev.timer fires daily 13:20 UTC every calendar day,
  // a cheap conditional check of the quarterly Fed Z.1 household leverage
  // source (weekend and unchanged-day runs still heartbeat), so a uniform
  // 26h window matches its margin-debt sibling. Data age of 100+ days is a
  // legitimate quarterly lag, never writer health. FRED HTTP only, no IB.
  "hhlev": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``model-catalog``: radon-model-catalog.timer fires daily 03:10 UTC every
  // calendar day, refreshing the chat picker's frontier model per LLM
  // provider whose API key is present. Provider releases are not a market
  // cadence, so weekend runs heartbeat like any other and a uniform 26h
  // window applies. Provider HTTP only, no IB.
  "model-catalog": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``vixts`` — radon-vixts.timer fires daily 02:45 UTC every calendar day,
  // ten minutes behind radon-vixcor so the Cboe CDN hits stay staggered
  // (weekend and holiday runs are 304 heartbeats), so a uniform 26h window
  // matches its cor / vixcor / straddle siblings. Cboe CDN CSVs only — no IB.
  "vixts": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``trin`` — radon-trin.timer samples NYSE A/D + volume from IB every 5 minutes during RTH (3 missed cycles flag); off-hours the close heartbeat holds a day.
  // R-122: 24h closed against a Mon-Fri-only timer went stale every Saturday
  // evening. 3 days covers the Fri -> Mon gap, like the other RTH writers.
  "trin": { open: 15 * MIN, extended: 3 * DAY, closed: 3 * DAY, category: "scheduled", requires_ib: true },

  // ``skew`` publishes every 5 minutes during RTH (radon-skew.timer) and
  // finalizes daily at 21:45 UTC. Ten minutes = two timer cycles, so one slow
  // run never reads as a dead live writer; the daily heartbeat preserves the
  // off-hours window.
  "skew": { open: 10 * MIN, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``skew2d`` — radon-skew2d.timer fires daily 21:50 UTC every calendar day
  // (derived from skew_history; weekend runs heartbeat when parent is quiet),
  // so a uniform 26h window fits like margin-debt / yield-curve / straddle.
  // Turso skew_history transform only — no IB.
  "skew2d": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``vol-cone`` — daily 20:45 UTC timer, UW-only, 26h open / 3d closed.
  "vol-cone": { open: 26 * HOUR, extended: 3 * DAY, closed: 3 * DAY, category: "scheduled", requires_ib: false },

  // ``vol-cone-intraday`` — 15m live UW sample during ET trading hours; a
  // market-hours-only writer is silent by design off-session, so extended
  // and closed carry the same 3d floor as its EOD parent.
  "vol-cone-intraday": { open: 45 * MIN, extended: 3 * DAY, closed: 3 * DAY, category: "scheduled", requires_ib: false },

  // ``knowledge-ingest`` — hourly knowledge-base ingest oneshot
  // (scripts/knowledge/ingest.py via radon-knowledge.timer, 24/7; no
  // market-hours gate — connectors read Turso + repo files). Heartbeats
  // every run including no-change short-circuits, so a uniform 26h window
  // (24h grace + timer jitter) flags a full day of missed runs without
  // paging on a single failed hour. Turso + Cerebras + local ONNX — no IB.
  "knowledge-ingest": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // cri-scan + vcg-scan run on Mon-Fri-only systemd timers (see CLAUDE.md
  // autonomous timers table). Closed-hour window must cover the
  // Fri-end → Mon-open gap (~65h) or a quiet weekend flips the banner.
  // Surfaced 2026-05-16: both flipped stale on a Saturday with clean
  // Friday-evening finishes because the prior 1-day closed window was
  // shorter than the weekend gap.
  "cri-scan": { open: 35 * MIN, extended: 3 * DAY, closed: 3 * DAY, category: "scheduled", requires_ib: true },
  // ``gex-scan`` still flows through ``record_service_health`` only when
  // a user POSTs the scan endpoint, so it's on-demand for banner purposes.
  // Source: scripts/gex_scan.py uses UWClient only — no IB dependency.
  // SCHEDULED, not on-demand: data_refresh's 15-minute RTH driver runs it, so
  // the same windows as its vcg-scan sibling. R-422.
  "gex-scan": { open: 15 * MIN, extended: 3 * DAY, closed: 3 * DAY, category: "scheduled", requires_ib: false },
  "gamma-rotation-scan": { open: 30 * MIN, extended: 30 * MIN, closed: 1 * DAY, category: "on-demand", requires_ib: false },
  // ``breadth-scan`` is SCHEDULED, not on-demand: radon-breadth.timer fires
  // every 5 min across ET trading hours. It was catalogued on-demand, which
  // coerces a past-window row into the informational `dormant` state instead
  // of the degraded `stale` one — and it was absent from the watchdog catalog
  // entirely, so nothing on either side noticed a frozen feed. NYSE A/D +
  // TICK internals sampled from IB index feeds, so requires_ib=true. Windows
  // mirror scripts/watchdog/services.py. R-236.
  "breadth-scan": { open: 15 * MIN, extended: 30 * MIN, closed: 3 * DAY, category: "scheduled", requires_ib: true },
  // ``vcg-scan`` has an autonomous 5-min cadence during market hours
  // (radon-vcg-refresh.timer / com.radon.vcg-refresh). The 15-min open
  // window tolerates 3 missed cycles before flagging — long enough to
  // absorb transient FastAPI or IB Gateway blips, short enough to
  // surface a real outage well inside the trading day. Closed window
  // covers the weekend gap (see cri-scan note above).
  "vcg-scan": { open: 15 * MIN, extended: 3 * DAY, closed: 3 * DAY, category: "scheduled", requires_ib: true },
  // ``cta-sync`` has an autonomous Mon-Fri schedule on the VPS
  // (radon-cta-sync.timer fires 18:15, 19:00, 21:30 UTC) plus the
  // laptop launchd plist as a redundant local trigger. Stale > 25h
  // means the timer failed across both regimes; 25h tolerates a long
  // weekend (Friday 21:30 UTC → Monday 18:15 UTC ≈ 69h) plus any
  // single missed firing.
  // MenthorQ source via Playwright — no IB dependency.
  "cta-sync": { open: 25 * HOUR, extended: 25 * HOUR, closed: 72 * HOUR, category: "scheduled", requires_ib: false },
  // ``menthorq-cta`` is the EXTRACTION-quality row written by the same
  // radon-cta-sync.timer firings (R-297): `error` when more than half the
  // rows lost their percentile to the z-score check. Same cadence as
  // ``cta-sync`` above, so the same windows apply.
  "menthorq-cta": { open: 25 * HOUR, extended: 25 * HOUR, closed: 72 * HOUR, category: "scheduled", requires_ib: false },

  // Market-hours-only writers: triggered by the FastAPI scan endpoints
  // during the trading day, dormant on nights and weekends. The
  // ``closed`` window has to be wide enough to bridge a full weekend
  // (Friday 16:00 ET → Monday 09:30 ET ≈ 65h) without flipping to
  // stale. Per-service intraday cadence varies but ≤30 min during
  // market hours catches genuine outages quickly.
  // scanner / discover / flow-analysis: radon-flow-refresh.timer hourly
  // ET RTH (plus SCAN). Wrapper skips closed session without a
  // heartbeat, so Monday mornings are a ~66-90h-old row — 4d windows
  // match theta-harvester. UW-only, no IB.
  // analyst-ratings: IB-primary with UW fallback; classified false so
  // IB-down alert grouping stays accurate — the writer still records a
  // healthy ok row when IB is unreachable but UW serves the data.
  "scanner": { open: 4 * DAY, extended: 4 * DAY, closed: 4 * DAY, category: "scheduled", requires_ib: false },
  // ``theta-harvester`` and ``strength-confirmation`` are fired
  // autonomously by radon-signals-refresh.timer (hourly, Mon-Fri
  // 09:00-16:00 ET) as well as by user POSTs, so they are SCHEDULED —
  // on-demand category excluded them from the watchdog buckets and a dead
  // timer froze the Top-candidates panel silently (R-068). UW-only data
  // paths; no IB dependency. Windows follow the bpi-scan precedent: the
  // wrapper skips outside market hours without heartbeating, so Monday
  // (and post-holiday) mornings legitimately serve a ~66-90h-old row —
  // uniform 4d pages on a dead timer without false-paging every Monday.
  // R-187: 4d was 96x an HOURLY cadence — a timer dead on Monday morning
  // went unreported until Thursday. Both are in RTH_ONLY_SERVICES below, so
  // `isStale` caps the effective age at how long the session has been open
  // and the tight open window cannot false-page at 09:31 off Friday's close.
  "theta-harvester": { open: 3 * HOUR, extended: 4 * DAY, closed: 4 * DAY, category: "scheduled", requires_ib: false },
  "strength-confirmation": { open: 3 * HOUR, extended: 4 * DAY, closed: 4 * DAY, category: "scheduled", requires_ib: false },
  "discover": { open: 4 * DAY, extended: 4 * DAY, closed: 4 * DAY, category: "scheduled", requires_ib: false },
  "flow-analysis": { open: 4 * DAY, extended: 4 * DAY, closed: 4 * DAY, category: "scheduled", requires_ib: false },
  "analyst-ratings": { open: 30 * MIN, extended: 30 * MIN, closed: 3 * DAY, category: "on-demand", requires_ib: false },
  // ``chronos-forecast`` only writes when a user POSTs /forecast/chronos
  // (Chronos-2 time-series engine, scripts/chronos_forecast.py). Reads
  // ticker_flow_history from Turso + runs the model locally — UW/DB-only,
  // no IB dependency. On-demand: past-window silence just means nobody
  // asked for a forecast, so it must not fire the degraded banner. Closed
  // window bridges a full weekend like its UW-scan siblings.
  "chronos-forecast": { open: 30 * MIN, extended: 30 * MIN, closed: 3 * DAY, category: "on-demand", requires_ib: false },
  // ``flow-surprise`` only writes when a user POSTs /flow-surprise (Feature 2
  // residual scan, scripts/flow_surprise.py). DB-only like its UW-scan siblings.
  "flow-surprise": { open: 30 * MIN, extended: 30 * MIN, closed: 3 * DAY, category: "on-demand", requires_ib: false },
  // ``informed-flow`` only writes when a user hits FastAPI
  // GET /informed-flow/{ticker} — the subprocess bridge runs
  // scripts/fetch_informed_flow.py on demand (congress + insider +
  // institutional). UW-only: the script imports clients.uw_client and
  // never touches IB, so requires_ib=false. Same window as its UW-scan
  // siblings above.
  "informed-flow": { open: 30 * MIN, extended: 30 * MIN, closed: 3 * DAY, category: "on-demand", requires_ib: false },
  // ``catalysts`` runs three times per trading day (06:30, 10:00, 16:00 ET)
  // and heartbeats ok on holiday skips (run_catalysts.sh). Seven hours spans
  // the longest active-day interval; four days bridges a long weekend.
  // UW-only (fetch_catalysts.py imports clients.uw_client) — no IB.
  // Shipped 2026-07-02 without registration and inherited the 1h default,
  // flagging stale every afternoon.
  "catalysts": { open: 7 * HOUR, extended: 7 * HOUR, closed: 4 * DAY, category: "scheduled", requires_ib: false },
  // ``bpi-scan`` runs Mon-Fri 21:30 UTC (radon-bpi.timer), AFTER the close:
  // during Monday's whole session the newest row is legitimately Friday
  // evening's (~72h old), so the window is a uniform 4d rather than a tight
  // open window (a 26h open window would flag stale every Monday). Holiday
  // runs still heartbeat (incremental no-op), so the gap never widens past
  // the weekend. Yahoo + Turso only — no IB.
  "bpi-scan": { open: 4 * DAY, extended: 4 * DAY, closed: 4 * DAY, category: "scheduled", requires_ib: false },
  // ``leap-scan`` runs once daily (radon-leap.timer) and via on-demand
  // dashboard refresh. Daily cadence so 26h covers a weekend (Fri →
  // Mon morning) without flipping stale; the on-demand button can
  // bring it fresh in between.
  "leap-scan": { open: 26 * HOUR, extended: 26 * HOUR, closed: 3 * DAY, category: "scheduled", requires_ib: false },
  // ``garch-scan`` runs on-demand via the dashboard plus an optional
  // scheduled timer (not yet shipped — when it lands, default to a
  // mid-session cadence). UW-only data flow, no IB dependency.
  "garch-scan": { open: 26 * HOUR, extended: 26 * HOUR, closed: 3 * DAY, category: "scheduled", requires_ib: false },

  // Retain the watchdog's 24h window for installations that still have an
  // embedded replica. Server response boundaries suppress this row entirely
  // when data/replica.db is absent; the static entry remains so a present
  // replica is still monitored and the Python/TypeScript catalogs stay aligned.
  "replica-watchdog": { open: 24 * HOUR, extended: 24 * HOUR, closed: 24 * HOUR, category: "scheduled", requires_ib: false },
  // ``watchdog-alerts`` is event-driven: a quiet interval is healthy, so a
  // tight window would turn the last dispatched alert into false stale noise.
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
  // probe reads it). OPEN window = 5 * MIN: during RTH the relay writes a
  // heartbeat every 60s, so 5 missed writes means a DEAD relay PROCESS —
  // coerce it to stale within minutes instead of a full day (the 24h open
  // window let the 2026-06-18 outage hide as "nominal" for hours). The
  // escalation error row lands well before 5 min (45s threshold + K=3
  // reconnect cycles ≈ 2.25 min), so a normal ladder episode reddens via
  // its own error state, never via this staleness floor.
  // EXTENDED + CLOSED stay 24h: off-hours a healthy relay writes nothing
  // (the heartbeat is RTH-only, matching isUSMarketHours), so a tight
  // off-hours window would flip "silent = healthy" to stale every night.
  // requires_ib MUST be false: the relay alert is precisely the signal
  // that the IB data plane is dead, so grouping it under the IB-outage
  // umbrella (which requires_ib=true does) would suppress the very alert
  // we want.
  "ib-realtime-relay": { open: 5 * MIN, extended: 24 * HOUR, closed: 24 * HOUR, category: "scheduled", requires_ib: false },

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

  // ``flow-refresh`` is the hourly RTH dark-pool flow driver
  // (scripts/run_flow_refresh.sh via radon-flow-refresh.timer,
  // Mon..Fri 09..16:00 ET). It has always written its own ok/error row
  // through write_service_health_http; it was in NEITHER catalog, so nothing
  // aged it. Surfaced by R-412's widened resolver.
  "flow-refresh": { open: 3 * HOUR, extended: 4 * DAY, closed: 4 * DAY, category: "scheduled", requires_ib: false },

  // ``forecast-nightly`` is the Chronos-2 backfill + calibration
  // (scripts/nightly_forecast.py via radon-forecast-nightly.timer, 07:00 UTC,
  // 24/7). R-402: it wrote no row on any path, so a throwing backfill left the
  // forecast tables silently not advancing with nothing at the edge saying so.
  "forecast-nightly": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

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

  // ``performance`` is a mirror-fed scan (db.scan_mirror SNAPSHOT_UPSERTS)
  // that only runs when a user hits its FastAPI scan endpoint — on-demand
  // like scanner/discover. IB-primary with cache/UW/Yahoo fallbacks and
  // still records ok when IB is unreachable, so requires_ib=false.
  "performance": { open: 30 * MIN, extended: 30 * MIN, closed: 3 * DAY, category: "on-demand", requires_ib: false },
  // ``oi-changes`` is mirror-fed (fetch_oi_changes.py --market →
  // mirror_scan_snapshot → upsert_oi_changes + service_health). Scheduled
  // by radon-oi-changes.timer 3x per US trading day (14:00 / 17:00 / 20:00
  // UTC Mon-Fri); run_oi_changes_refresh.sh heartbeats ok on holiday skips.
  // UW-only. 26h open covers a missed mid-day fire; closed 3d absorbs Fri→Mon.
  "oi-changes": { open: 26 * HOUR, extended: 26 * HOUR, closed: 3 * DAY, category: "scheduled", requires_ib: false },

  // ``preset-rebalance`` runs WEEKLY inside the monitor daemon (index
  // constituent refresh, Sundays). It shipped for months with no
  // service_health row at all; the DUR-14 structural heartbeat in
  // BaseHandler.run() covers it now. 8-day uniform window = weekly cadence
  // + one day of daemon-restart drift. UW/static data only — no IB.
  "preset-rebalance": { open: 8 * DAY, extended: 8 * DAY, closed: 8 * DAY, category: "scheduled", requires_ib: false },

  // ``journal-reconcile`` is the JRN-01 cross-check handler: daily scan of
  // executed_orders vs journal to detect silent DB-upsert drops. Runs 24/7
  // inside the monitor daemon (requires_market_hours=False). Pure Turso read
  // — no IB dependency. 26h window = daily cadence + timer jitter; weekends
  // are normal run days so no wide closed window is needed.
  "journal-reconcile": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``journal-expiry-sweep`` writes deterministic $0.00 expiration close rows
  // for option positions left open past expiry (expiration emits no execution,
  // so no fill-driven path ever closes the opener — SPCX 2026-08-07 incident).
  // Daily inside the monitor daemon (requires_market_hours=False). Pure Turso
  // read/write — no IB dependency. 26h window = daily cadence + timer jitter.
  "journal-expiry-sweep": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },

  // ``journal-gap-sli`` is the continuous gap SLI (R6): same executed_orders
  // vs journal detection as journal-reconcile, every 5 minutes, with
  // structured missing_exec_id_count in service_health.last_error. Pure
  // Turso — no IB. 15-min window = 3 missed cycles before stale.
  "journal-gap-sli": { open: 15 * MIN, extended: 15 * MIN, closed: 15 * MIN, category: "scheduled", requires_ib: false },

  // ``grok-page-responder`` is the VPS P1 auto-fix poller
  // (scripts/grok_page_responder.py via radon-grok-page-responder.timer,
  // every 30s, 24/7). Pure Turso + Pushover — no IB. Only a cycle that
  // COMPLETES heartbeats; cycles that skip on a live lock deliberately stay
  // silent, so the window has to absorb one full grok run
  // (GROK_TIMEOUT_SECS = 1h) plus the ticket bookkeeping around it. 90m.
  // Without this row a wedged poller was invisible for 2h40m (2026-08-14).
  "grok-page-responder": { open: 90 * MIN, extended: 90 * MIN, closed: 90 * MIN, category: "scheduled", requires_ib: false },

  // ``portfolio-archive`` is the portfolio_snapshots cold-archive oneshot
  // (scripts/archive_portfolio_snapshots.py via radon-portfolio-archive.timer
  // on the VPS, 06:52 UTC daily, before db-backup). Heartbeats ok/error on
  // every completed run. 48h window matches db-backup: one missed night
  // pages before the second archive is lost. Turso + local partitions
  // (+ optional S3) — no IB dependency.
  "portfolio-archive": { open: 48 * HOUR, extended: 48 * HOUR, closed: 48 * HOUR, category: "scheduled", requires_ib: false },

  // ``db-retention`` is the daily keep-latest sweep for append-only scan
  // snapshot tables (scripts/db_retention_sweep.py via
  // radon-db-retention.timer, 07:22 UTC). Does not touch portfolio_snapshots
  // or journal. 48h window matches other nightly DB jobs.
  "db-retention": { open: 48 * HOUR, extended: 48 * HOUR, closed: 48 * HOUR, category: "scheduled", requires_ib: false },

  // ``media-backup`` is the nightly off-box mirror of the media.radon.run
  // tree (cloud/scripts/media_backup.py via radon-media-backup.timer,
  // 10:15 UTC). Uploads /home/radon/radon-cloud/media to B2 prefix media/
  // using RADON_ARCHIVE_S3_* (or RADON_MEDIA_BACKUP_S3_*). Heartbeats
  // ok/error every run. 48h window matches db-backup: one missed night
  // pages before the second is lost. Local disk + B2 only — no IB.
  "media-backup": { open: 48 * HOUR, extended: 48 * HOUR, closed: 48 * HOUR, category: "scheduled", requires_ib: false },

  // Equibles writers call record_service_health directly (no service_cycle).
  // Shipped without registration and inherited the 1h default, coercing
  // successful ok rows to stale during RTH (2026-08-13). Equibles API only
  // — no IB. RandomizedDelaySec=300 on every timer is absorbed below.
  // Daily calendar-day timers (weekend runs heartbeat): uniform 26h.
  "equibles-short-crowding": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },
  "equibles-filing-forensics": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },
  // Weekly timers: 8-day uniform window = weekly cadence + one day of
  // drift, same class as preset-rebalance.
  "equibles-13f": { open: 8 * DAY, extended: 8 * DAY, closed: 8 * DAY, category: "scheduled", requires_ib: false },
  "equibles-ats-venue-share": { open: 8 * DAY, extended: 8 * DAY, closed: 8 * DAY, category: "scheduled", requires_ib: false },
  "equibles-cot-positioning": { open: 8 * DAY, extended: 8 * DAY, closed: 8 * DAY, category: "scheduled", requires_ib: false },

  // ``event-odds`` — laptop launchd Mon-Fri 07:00/11:00/15:00 local,
  // holiday-aware (scripts/run_event_odds.sh). Three weekday fires like
  // catalysts: 7h spans the active-day gaps; 4d bridges a long weekend.
  // Polymarket only — no IB.
  "event-odds": { open: 7 * HOUR, extended: 7 * HOUR, closed: 4 * DAY, category: "scheduled", requires_ib: false },

  // ``disk-cleanup`` is the WEEKLY root-filesystem reclaim on the VPS
  // (cloud/scripts/disk_cleanup.py via radon-disk-cleanup.timer, Sun 03:20
  // UTC) — stale deploy images, leaked release worktrees, npm/pip caches,
  // journald. Heartbeats ok/error on every run. Uniform 8d window on the
  // preset-rebalance weekly precedent: weekly cadence plus timer jitter, so
  // one missed Sunday surfaces before the second. Docker + local disk only.
  "disk-cleanup": { open: 8 * DAY, extended: 8 * DAY, closed: 8 * DAY, category: "scheduled", requires_ib: false },

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
  if (market === "open" && RTH_ONLY_SERVICES.has(service)) {
    const et = new Date(new Date(nowMs).toLocaleString("en-US", { timeZone: "America/New_York" }));
    const elapsedOpenMs = Math.max(0, (et.getHours() * 60 + et.getMinutes() - (9 * 60 + 30)) * MIN);
    const todayOpenEt = nowMs - elapsedOpenMs;
    if (ts < todayOpenEt) return elapsedOpenMs > window;
  }
  return nowMs - ts > window;
}

const RTH_ONLY_SERVICES = new Set([
  // R-187: written hourly by radon-signals-refresh.timer during the session
  // only, so their open-window age must be measured from today's open.
  "theta-harvester",
  "strength-confirmation",
  // Same shape: radon-flow-refresh.timer is Mon..Fri 09..16:00 ET.
  "flow-refresh",
  "orders-sync",
  "portfolio-sync",
  "journal-sync",
  "fill-monitor",
  "position-reconcile",
  "cri-scan",
  "vcg-scan",
  "ib-realtime-relay",
]);

/** ET calendar date ("YYYY-MM-DD") is a full-closure US market holiday per
 * the static table (scripts/config/market_holidays.json — the same SoT the
 * relay's marketCalendar.js consults). Early-close half-days are NOT here;
 * they only make afternoon windows conservatively tight, never noisy-wide.
 * Years missing from the table fall back to weekday-only (pre-2026-07-03
 * behavior) rather than guessing. */
function isUsMarketHoliday(et: Date): boolean {
  const isoDate = `${et.getFullYear()}-${String(et.getMonth() + 1).padStart(2, "0")}-${String(et.getDate()).padStart(2, "0")}`;
  return isHolidayIso(isoDate);
}

/**
 * R-164: the static table is finite, and `isHolidayIso` returns false for any
 * year it does not cover — so once it runs out, `isUsTradingDay` silently
 * degrades to weekday-only and every full-closure holiday reads as a trading
 * day. Blanking a whole uncovered year would be far worse than being wrong on
 * ~9 days, so the runtime behaviour is unchanged and the coverage is instead
 * made explicit here and pinned by a contract test that fails CI a year
 * before the data runs out.
 */
export const HOLIDAY_TABLE_YEARS: readonly string[] = Object.freeze(
  Object.keys(staticHolidays as Record<string, string[]>).sort(),
);

export function isHolidayTableCovering(isoDate: string): boolean {
  return HOLIDAY_TABLE_YEARS.includes(isoDate.slice(0, 4));
}

function isHolidayIso(isoDate: string): boolean {
  const dates = (staticHolidays as Record<string, string[]>)[isoDate.slice(0, 4)];
  return Array.isArray(dates) && dates.includes(isoDate);
}

/**
 * Date-only trading-day check against the same holiday SoT: a weekday that is
 * not a full-closure US market holiday. Pure on the ISO "YYYY-MM-DD" string —
 * the weekday is derived at UTC noon so the calendar date never shifts with
 * the host timezone.
 */
export function isUsTradingDay(isoDate: string): boolean {
  const [y, m, d] = isoDate.split("-").map(Number);
  const weekday = new Date(Date.UTC(y, m - 1, d, 12)).getUTCDay();
  if (weekday === 0 || weekday === 6) return false;
  return !isHolidayIso(isoDate);
}

/**
 * Server-side market-state derivation, mirrored from
 * web/lib/useMarketHours.ts but pure (no React, no setInterval).
 *
 * Returns the current MarketState in America/New_York. Full-closure
 * holidays read as "closed" all day (an observed holiday that computed
 * as "open" applied the tight RTH windows to scan writers that correctly
 * did not run — 6-7 stale rows of footer noise on 2026-07-03).
 */
export function getMarketStateFromDate(now: Date = new Date()): MarketState {
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = et.getDay(); // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return "closed";
  if (isUsMarketHoliday(et)) return "closed";

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
  if (isUsMarketHoliday(et)) return "closed";

  const minutes = et.getHours() * 60 + et.getMinutes();
  if (minutes >= 9 * 60 + 30 && minutes <= 16 * 60) return "open";
  if (minutes >= 4 * 60 && minutes < 9 * 60 + 30) return "pre";
  if (minutes > 16 * 60 && minutes <= 20 * 60) return "after";
  return "closed";
}
