"""Catalog of `scheduled` services the watchdog tracks.

Mirrors web/lib/serviceHealthWindows.ts (only entries with
``category: "scheduled"``). A contract test in
``scripts/tests/test_watchdog/test_services.py`` reads the TS file via
regex and asserts both lists agree, so a future SOT drift between TS
and Python is caught by CI.

Each entry carries the open/closed freshness windows (in seconds) the
watchdog uses to decide whether a row is past-window. Numbers match the
TS source ms values divided by 1000.

Buckets group services by cadence so a single systemd timer fires the
right batch at the right interval:

 * ``intraday`` — market-hours-only writers; gate the timer to ET
   trading hours and re-check the gate in Python before firing.
 * ``continuous`` — always-on writers; the timer fires every 5 min,
   24/7.
 * ``daily`` — once-per-day writers; the timer fires hourly so any
   delay surfaces within 1h of the open-state window expiring.
 * ``error`` — orthogonal sweep; flags any scheduled service in
   ``state == 'error'`` past 2 consecutive cycles regardless of
   staleness.
"""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict

_MIN = 60
_HOUR = 60 * _MIN
_DAY = 24 * _HOUR
REPLICA_PATH = Path(__file__).resolve().parents[2] / "data" / "replica.db"


class FreshnessWindow(TypedDict):
    open: int
    closed: int
    # True when the writer's data-flow depends on IB Gateway. The watchdog
    # groups alerts on these services into a single "IB Gateway awaiting
    # 2FA" message when ``/health.auth_state`` flags an upstream IB
    # problem — see scripts/watchdog/check.py:check_bucket. False for
    # writers that talk to UW, Flex Web Service, Playwright sources, or
    # nothing IB-related; their alerts are routed normally.
    #
    # The flag is verified against each writer's source code, not against
    # an aspirational taxonomy — see test_services.py contract tests.
    requires_ib: bool


# Every scheduled service in web/lib/serviceHealthWindows.ts.
# Windows in seconds.
SCHEDULED_SERVICES: dict[str, FreshnessWindow] = {
    "newsfeed-scraper": {"open": 5 * _MIN, "closed": 5 * _MIN, "requires_ib": False},
    # radon-nextjs-db-watchdog fires every 60s around the clock (writer:
    # cloud/scripts/nextjs_db_watchdog.py). Registered so a wedged or
    # never-firing watchdog is itself staleness-checked — without this its
    # row could sit stale forever with nobody watching (REL-033).
    "nextjs-db-read":   {"open": 5 * _MIN, "closed": 5 * _MIN, "requires_ib": False},
    # R-325: three timer-backed units wrote no service_health row and were in
    # neither catalog, so a job that stopped firing was invisible to check.py.
    # Each now heartbeats and is staleness-checked on its own cadence.
    #
    # radon-refresh.timer: Mon-Fri 13..21:00,15,30,45 UTC (every 15 min through
    # RTH). Open window tolerates two missed fires; the job self-skips holidays
    # and weekends, so the closed window absorbs the whole weekend.
    "data-refresh":     {"open": 35 * _MIN, "closed": 3 * _DAY, "requires_ib": False},
    # radon-incident-watchdog.timer: every 5 min, around the clock.
    "incident-watchdog": {"open": 15 * _MIN, "closed": 15 * _MIN, "requires_ib": False},
    # radon-demo-mirror.timer: Mon-Fri 21:45 UTC, once a day. Closed window
    # absorbs the weekend gap between Friday's and Monday's runs.
    "demo-mirror":      {"open": 26 * _HOUR, "closed": 4 * _DAY, "requires_ib": False},
    # Market-hours-only writers (gated by MonitorDaemon's
    # requires_market_hours=True). Mirrors web/lib/serviceHealthWindows.ts;
    # closed window absorbs the longest weekend gap so the banner doesn't
    # fire overnight. extended is folded into closed on the TS side for
    # the same reason — writers don't run in extended hours.
    "orders-sync":      {"open": 10 * _MIN, "closed": 3 * _DAY, "requires_ib": True},
    "portfolio-sync":   {"open": 10 * _MIN, "closed": 3 * _DAY, "requires_ib": True},
    "journal-sync":     {"open": 10 * _MIN, "closed": 3 * _DAY, "requires_ib": True},
    # cash-flow-sync fires at 17:00 ET on trading days only; skips weekends
    # + US holidays. Longest legit gap: Fri 17:00 ET → Mon 17:00 ET ≈ 72h.
    # Prior 25h closed window tripped every Saturday. Widened to 4 days.
    "cash-flow-sync":   {"open": 25 * _HOUR, "closed": 4 * _DAY, "requires_ib": False},
    # execution-sweep fires at 20:30 ET on trading days only (evening
    # after-hours fill import, REL-012); skips weekends + US holidays.
    # Longest legit gap: Fri 20:30 ET → Mon 20:30 ET ≈ 72h, so closed is
    # 4 days like cash-flow-sync; open 26h catches a missed weekday run.
    "execution-sweep":  {"open": 26 * _HOUR, "closed": 4 * _DAY, "requires_ib": True},
    "fill-monitor":     {"open": 5 * _MIN, "closed": 3 * _DAY, "requires_ib": True},
    # position-reconcile — 30-min RTH IB-vs-snapshot drift check (REL-001).
    # 45min open = one missed cycle + slack; mirrors serviceHealthWindows.ts.
    "position-reconcile": {"open": 45 * _MIN, "closed": 3 * _DAY, "requires_ib": True},
    # R-159: radon-perf-twr wrote no service_health row and always exited 0,
    # and was in NEITHER catalog (the web catalog's `performance` key is a
    # different, on-demand writer). On a Flex 1025 lockout the builder
    # returns status=degraded, systemd records success, and check.py treats
    # "no row" as dormant — /performance serves stale returns with no alert.
    # radon-perf-twr.timer fires Tue..Sat 07:30 ET; 26h open catches a missed
    # weekday run, 4d closed covers the Sat->Tue gap like cash-flow-sync.
    "perf-twr":         {"open": 26 * _HOUR, "closed": 4 * _DAY, "requires_ib": False},
    # radon-flex-pull.timer Tue..Sat 07:30/08:30 ET. Empty outgoing is ok
    # through 2026-08-31, then error. Same weekend gap as perf-twr.
    "flex-pull":        {"open": 26 * _HOUR, "closed": 4 * _DAY, "requires_ib": False},
    "flex-token-check": {"open": 25 * _HOUR, "closed": 25 * _HOUR, "requires_ib": False},
    "menthorq-session": {"open": 25 * _HOUR, "closed": 25 * _HOUR, "requires_ib": False},
    # Daily LIVE probe of the MenthorQ credential re-login chain (the
    # metadata check above cannot see a broken chain). Playwright-only.
    "menthorq-login-probe": {"open": 25 * _HOUR, "closed": 25 * _HOUR, "requires_ib": False},
    # closed = 3d (not 1d) to cover the Fri->Mon weekend gap — these run on
    # Mon-Fri-only timers, so over the weekend they're legitimately silent.
    # Matches web/lib/serviceHealthWindows.ts.
    "cri-scan":         {"open": 35 * _MIN, "closed": 3 * _DAY, "requires_ib": True},
    "vcg-scan":         {"open": 15 * _MIN, "closed": 3 * _DAY, "requires_ib": True},
    # gex-scan — the 15-minute RTH driver runs it (data_refresh._SCRIPT_SERVICES
    # maps gex_scan.py -> "gex-scan"), but it was in neither this catalog nor
    # the error bucket derived from it, so check.py never evaluated it and a
    # failing gex_scan was invisible. Same windows as its vcg-scan sibling;
    # UWClient only, so no IB dependency. R-422.
    "gex-scan":         {"open": 15 * _MIN, "closed": 3 * _DAY, "requires_ib": False},
    # radon-breadth.timer fires every 5 min across ET trading hours into
    # run_breadth_scan.sh -> breadth_scan.py's mirror_scan_snapshot. It was in
    # NEITHER catalog, so it appeared in no BUCKETS list and check.py — which
    # iterates BUCKETS and nothing else — never evaluated it: disable the
    # timer and breadth froze silently, with the web banner staying
    # informational because its category said "on-demand". units.py is not a
    # backstop; it fires only on ActiveState=failed. Same class as the
    # nextjs-db-read registration above (REL-033). R-236.
    "breadth-scan":     {"open": 15 * _MIN, "closed": 3 * _DAY, "requires_ib": True},
    "cta-sync":         {"open": 25 * _HOUR, "closed": 72 * _HOUR, "requires_ib": False},
    # Extraction-quality row written by the same radon-cta-sync.timer firings
    # (R-297): `error` when more than half the rows lost their percentile to
    # the z-score check. Same cadence as cta-sync, so the same windows.
    "menthorq-cta":     {"open": 25 * _HOUR, "closed": 72 * _HOUR, "requires_ib": False},
    # Daily-cadence writers (mirror web/lib/serviceHealthWindows.ts):
    #  * llm-token-index — radon-llm-index.timer, once/UTC-day 06:30; pulls
    #    Artificial Analysis only, no IB. Has not fired on weekends in
    #    practice; closed widened to 4d to cover Fri-06:30 → Mon-06:30 gap.
    #  * leap-scan       — radon-leap.timer, once daily 14:00 UTC + on-demand;
    #    UW-only. 26h open covers a weekend, 3d closed the long gap.
    #  * garch-scan      — radon-garch.timer 3x/RTH day + on-demand dashboard;
    #    UW-only. Same daily windows as leap-scan.
    #  * oi-changes      — radon-oi-changes.timer 3x/RTH day (14/17/20 UTC
    #    Mon-Fri); market-wide fetch_oi_changes.py --market heartbeats via
    #    mirror_scan_snapshot; holiday skips heartbeat ok. UW-only.
    #  * catalysts      — radon-catalysts.timer at 06:30, 10:00, and 16:00 ET;
    #    heartbeats ok on holiday skips (run_catalysts.sh). Seven hours spans
    #    the active-day schedule; four days bridges a long weekend. UW-only.
    "llm-token-index":  {"open": 25 * _HOUR, "closed": 4 * _DAY, "requires_ib": False},
    # margin-debt — daily wrapper every calendar day (FINRA monthly source,
    # conditional-GET no-op on unchanged days, heartbeats each run). Uniform
    # 26h window: no weekend/holiday gap to widen for.
    "margin-debt":      {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # hhlev — radon-hhlev.timer, daily 13:20 UTC every calendar day (quarterly
    # Z.1 source with a daily cheap check, heartbeats each run). Uniform 26h
    # window cloned from margin-debt: no weekend/holiday gap to widen for.
    # Data age (a quarter can be ~100+ days old) is legitimate and never
    # conflated with writer health. FRED HTTP only — no IB dependency.
    "hhlev":            {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # model-catalog — radon-model-catalog.timer, daily 03:10 UTC every
    # calendar day (provider model lists move on release cadence, not market
    # cadence, so weekend runs heartbeat like any other). Uniform 26h window.
    # Provider HTTP only — no IB dependency.
    "model-catalog":    {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # yield-curve — radon-yield-curve.timer, daily 22:30 UTC every calendar
    # day (weekend/holiday runs heartbeat with no new Treasury rows). Uniform
    # 26h window: no weekend/holiday gap to widen for. treasury.gov + Yahoo
    # overlay — no IB dependency.
    "yield-curve":      {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # div-yield — radon-divyield.timer, daily 22:40 UTC every calendar day
    # (weekend/holiday runs are unchanged-day heartbeats). Uniform 26h
    # window: no weekend/holiday gap to widen for. GitHub/Wikipedia
    # constituents + Yahoo quotes + Turso y10 — no IB dependency.
    "div-yield":        {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # credit-spread — radon-credit-spread.timer, daily 21:45 UTC every calendar
    # day (weekend/holiday runs heartbeat with no new rows). Uniform
    # 26h window: no weekend/holiday gap to widen for. IB HYG + SPX first,
    # then UW, then Yahoo. Yahoo is a complete fallback so this is not
    # grouped with IB outages.
    "credit-spread":    {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # iei-hyg — radon-iei-hyg.timer, daily 21:55 UTC every calendar day
    # (weekend/holiday runs heartbeat with no new rows). Uniform 26h
    # window: no weekend/holiday gap to widen for. IB IEI + HYG + DXY first,
    # then UW, then Yahoo. Yahoo is a complete fallback so this is not
    # grouped with IB outages.
    "iei-hyg":          {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # hy-ad — radon-hyad.timer, Tue..Sat 11:00 UTC (T+1 morning after FINRA
    # TRACE end-of-day finalization; FINRA publishes only for days the bond
    # market is open, so weekend/holiday runs are unchanged-day heartbeats).
    # 120h windows cover 3-day weekends plus bond-market-only holidays before
    # the next Tue..Sat run lands. FINRA HTTP + Turso SPX join — no IB
    # dependency.
    "hy-ad":            {"open": 120 * _HOUR, "closed": 120 * _HOUR, "requires_ib": False},
    # trin — radon-trin.timer, every 5m Mon-Fri 13:02-21:57 UTC (offset 2 min
    # from breadth). Samples TRIN-NYSE from an IB snapshot during RTH only;
    # off-hours runs refresh the StockCharts daily series and heartbeat. 15m
    # open tolerates two missed slots; 24h closed covers the silent overnight
    # and the weekend is bridged by the daily-only heartbeats. IB is the ONLY
    # source of the hourly sample, so it groups with IB outages.
    # R-122: closed was 24h against a Mon-Fri-only timer with
    # Persistent=false, so the last heartbeat is Friday ~21:57 UTC and the row
    # went stale from Saturday evening until Monday's first fire — a
    # guaranteed weekend page. 3 days covers Fri -> Mon like the siblings.
    "trin":             {"open": 15 * _MIN, "closed": 3 * _DAY, "requires_ib": True},
    # straddle — radon-straddle.timer, daily 02:15 UTC every calendar day
    # (Cboe appends the session row ~20:00 ET; weekend/holiday runs are 304
    # heartbeats). Uniform 26h window mirrors margin-debt/yield-curve: no
    # weekend/holiday gap to widen for. Cboe CDN only — no IB dependency.
    "straddle":         {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # cor — radon-cor.timer, daily 02:20 UTC every calendar day (Cboe
    # overwrites the COR EOD files after each session; weekend/holiday runs
    # are 304 heartbeats). Uniform 26h window mirrors straddle: no
    # weekend/holiday gap to widen for. Cboe CDN only — no IB dependency.
    "cor":              {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # vixcor — radon-vixcor.timer, daily 02:35 UTC every calendar day, fifteen
    # minutes behind radon-cor so the COR3M row for the session already exists
    # (weekend/holiday runs are 304 heartbeats). Uniform 26h window mirrors its
    # cor parent. Cboe CDN plus Turso cor_history only — no IB dependency.
    "vixcor":           {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # vixts — radon-vixts.timer, daily 02:45 UTC every calendar day, in the
    # slot after straddle/cor/vixcor so the Cboe CDN hits stay staggered
    # (weekend/holiday runs are 304 heartbeats). Uniform 26h window mirrors
    # its Cboe siblings. Cboe CDN only — no IB dependency.
    "vixts":            {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # dispersion — radon-dispersion.timer, daily 22:20 UTC every calendar day,
    # after the 16:00 ET close year-round (weekend/holiday runs are no-new-
    # session heartbeats). Uniform 26h window matches its daily siblings. IB
    # primary with a Yahoo fallback, so the job heartbeats through an IB
    # outage: requires_ib stays False. A sweep IB served nothing on is `ok`
    # with last_error class `ib_rung_dead` (R-434), not an error bucket.
    "dispersion":       {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # ivrank — radon-ivrank.timer, daily 22:10 UTC every calendar day, after
    # the 16:00 ET close year-round (weekend/holiday runs are unchanged-data
    # heartbeats). Uniform 26h window matches its daily siblings. IB primary
    # with a UW fallback, so the job heartbeats through an IB outage:
    # requires_ib stays False.
    "ivrank":           {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # vol-cone — radon-vol-cone.timer, Mon-Fri 20:45 UTC after the 16:45 ET
    # close grace. UW greeks only — no IB. 26h open catches a missed weekday;
    # 3d closed covers Fri 20:45 UTC → Mon 20:45 UTC.
    "vol-cone":         {"open": 26 * _HOUR, "closed": 3 * _DAY, "requires_ib": False},
    # vol-cone-intraday — radon-vol-cone-intraday.timer, every 15m during ET
    # trading hours. Ranks a live UW sample against the stored cone so the
    # tab is tradeable during the session instead of a day stale. 45m open
    # tolerates one missed cycle; 3d closed covers Fri 16:00 ET -> Mon open,
    # since a market-hours-only writer is silent by design off-session.
    "vol-cone-intraday": {"open": 45 * _MIN, "closed": 3 * _DAY, "requires_ib": False},
    # skew — radon-skew.timer fires every 5 minutes during RTH plus a daily
    # 21:45 UTC finalization. 10m open = two timer cycles, so one slow or
    # skipped run never flips skew to stale.
    "skew":             {"open": 10 * _MIN, "closed": 26 * _HOUR, "requires_ib": False},
    # skew2d — radon-skew2d.timer, daily 21:50 UTC every calendar day
    # (five minutes after parent SKEW finalize; weekend/holiday runs heartbeat
    # with no new parent rows). Uniform 26h window mirrors margin-debt /
    # straddle: no weekend/holiday gap to widen for. Derived from Turso
    # skew_history only — no IB dependency.
    "skew2d":           {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},

    # knowledge-ingest — hourly knowledge-base ingest oneshot
    # (scripts/knowledge/ingest.py via radon-knowledge.timer, 24/7).
    # Uniform 26h window (24h grace + timer jitter) mirrors
    # web/lib/serviceHealthWindows.ts. Turso + Cerebras + local ONNX — no IB.
    "knowledge-ingest": {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    "leap-scan":        {"open": 26 * _HOUR, "closed": 3 * _DAY, "requires_ib": False},
    "garch-scan":       {"open": 26 * _HOUR, "closed": 3 * _DAY, "requires_ib": False},
    "oi-changes":       {"open": 26 * _HOUR, "closed": 3 * _DAY, "requires_ib": False},
    "catalysts":        {"open": 7 * _HOUR, "closed": 4 * _DAY, "requires_ib": False},
    # theta-harvester / strength-confirmation — fired autonomously by
    # radon-signals-refresh.timer (hourly, Mon-Fri 09:00-16:00 ET) as well
    # as by user POSTs (R-068). R-187: the uniform 4d window was 96x the
    # cadence, so a timer dead on Monday morning went unreported until
    # Thursday while the Top-candidates panel served a stale row. R-068 chose
    # 4d because the wrapper skips outside market hours without heartbeating
    # and Monday mornings legitimately serve a ~66-90h-old row — but these
    # two are in BUCKETS["intraday"], so `check.py`'s open-bell grace already
    # caps the effective age at how long the session has been open. The tight
    # OPEN window is therefore safe, and `closed` still absorbs the weekend.
    # UW-only, no IB.
    "theta-harvester":  {"open": 3 * _HOUR, "closed": 4 * _DAY, "requires_ib": False},
    "strength-confirmation": {"open": 3 * _HOUR, "closed": 4 * _DAY, "requires_ib": False},
    "scanner":          {"open": 4 * _DAY, "closed": 4 * _DAY, "requires_ib": False},
    "discover":         {"open": 4 * _DAY, "closed": 4 * _DAY, "requires_ib": False},
    "flow-analysis":    {"open": 4 * _DAY, "closed": 4 * _DAY, "requires_ib": False},
    # bpi-scan — radon-bpi.timer, Mon-Fri 21:30 UTC AFTER the close: during
    # Monday's whole session the newest row is legitimately Friday-evening's
    # (~72h old), so the window is uniform 4d rather than a tight open window.
    # Yahoo + Turso only, no IB.
    "bpi-scan":         {"open": 4 * _DAY, "closed": 4 * _DAY, "requires_ib": False},
    # ib-watchdog polls FastAPI /health every 60s and heartbeats a row each
    # cycle; 5-min window catches a dead watchdog process within minutes.
    # It MONITORS IB but does not depend on IB being healthy to run, so
    # requires_ib=False (suppressing it during an IB outage would defeat it).
    "ib-watchdog":      {"open": 5 * _MIN, "closed": 5 * _MIN, "requires_ib": False},
    # ib-realtime-relay writes a service_health heartbeat every ~60s during RTH
    # (DUR-16: markTick -> decideHealthWrite), so 5 missed writes (5*_MIN) means a
    # DEAD or WEDGED relay PROCESS, not normal quiet — matching the 5*MIN open
    # window in web/lib/serviceHealthWindows.ts. closed stays 24h: off-hours a
    # healthy relay writes nothing (extended folds into closed here, as elsewhere),
    # so a wide window avoids false stale flags. requires_ib=False is load-bearing:
    # the relay error IS the IB-data-plane-dead signal, so grouping it under
    # IB-outage suppression would mute the alert.
    "ib-realtime-relay": {"open": 5 * _MIN, "closed": 24 * _HOUR, "requires_ib": False},
    # Event-driven writer when a replica file exists. Match the 24h window
    # from web/lib/serviceHealthWindows.ts; applicability is checked at
    # evaluation time so a retired replica's historical row cannot alert.
    "replica-watchdog": {"open": 24 * _HOUR, "closed": 24 * _HOUR, "requires_ib": False},
    # ``watchdog-alerts`` is the meta-row this very service writes when
    # alerting on OTHER services. Same event-driven shape as
    # replica-watchdog — 24h window. NOT included in any bucket below
    # to avoid recursive alerting (watchdog alerting on its own alerts row).
    "watchdog-alerts":  {"open": 24 * _HOUR, "closed": 24 * _HOUR, "requires_ib": False},
    # config-drift — daily VPS configuration-drift audit (radon-cloud
    # scripts/drift_audit.py via radon-drift-audit.timer). Heartbeats
    # ok/error every run, 24/7 incl. weekends, so the window is a
    # uniform 26h (cadence + timer jitter). Filesystem + systemctl
    # only — no IB dependency.
    "config-drift":     {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # flow-refresh — hourly RTH driver behind the dark-pool flow tables
    # (scripts/run_flow_refresh.sh via radon-flow-refresh.timer,
    # Mon..Fri 09..16:00 ET). It has always written its own ok/error row
    # through write_service_health_http; it was in NEITHER catalog, so
    # nothing aged it. Surfaced by R-412's widened resolver.
    "flow-refresh":     {"open": 3 * _HOUR, "closed": 4 * _DAY, "requires_ib": False},
    # forecast-nightly — Chronos-2 backfill + calibration
    # (scripts/nightly_forecast.py via radon-forecast-nightly.timer,
    # 07:00 UTC, 24/7). R-402: the unit wrote no row on any path, so a
    # throwing backfill left the forecast tables silently not advancing.
    "forecast-nightly": {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # db-backup — nightly full Turso dump on the VPS (radon-cloud
    # scripts/db_backup.py via radon-db-backup.timer, 07:52 UTC, 24/7).
    # Heartbeats ok/error every run with size + duration detail. 48h
    # window mirrors web/lib/serviceHealthWindows.ts: one missed night
    # alerts before the second dump is lost. Turso + local disk — no IB.
    "db-backup":        {"open": 48 * _HOUR, "closed": 48 * _HOUR, "requires_ib": False},
    # host-metrics — minute-cadence host/process sampler on the VPS
    # (main-repo scripts/host_metrics_sampler.py via radon-cloud
    # radon-host-metrics.timer, DUR-12). Heartbeats ok/error every run,
    # 24/7; uniform 10-min window mirrors web/lib/serviceHealthWindows.ts.
    # Reads /proc + systemctl + /health/lite only — no IB dependency.
    "host-metrics":     {"open": 10 * _MIN, "closed": 10 * _MIN, "requires_ib": False},
    # preset-rebalance — WEEKLY index-constituent refresh inside the
    # monitor daemon (Sundays). Heartbeats via the DUR-14 structural
    # BaseHandler.run() write. 8-day window = weekly cadence + one day of
    # daemon-restart drift. UW/static data only — no IB. In the daily
    # bucket: the hourly check surfaces a missed week within 1h of the
    # window expiring.
    "preset-rebalance": {"open": 8 * _DAY, "closed": 8 * _DAY, "requires_ib": False},
    # journal-reconcile — daily cross-check of executed_orders vs journal
    # (JRN-01). Detects silent DB-upsert drops. Runs 24/7 inside the
    # monitor daemon (requires_market_hours=False). Pure Turso read — no IB
    # dependency. 26h window = daily cadence + timer jitter; weekends are
    # normal run days so no wide closed window is needed.
    "journal-reconcile": {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # journal-expiry-sweep — daily synthetic $0.00 close rows for option
    # positions left open past expiry (expiration emits no execution).
    # Runs 24/7 inside the monitor daemon. Pure Turso read/write — no IB
    # dependency. 26h window = daily cadence + timer jitter.
    "journal-expiry-sweep": {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    # journal-gap-sli — continuous (5m) executed_orders vs journal gap SLI.
    # Structured missing_exec_id_count in last_error. Pure Turso, no IB.
    # 15m window = 3 missed cycles before stale.
    "journal-gap-sli": {"open": 15 * _MIN, "closed": 15 * _MIN, "requires_ib": False},
    # grok-page-responder — VPS P1 auto-fix poller (30s, 24/7). Pure Turso +
    # Pushover, no IB. Only a COMPLETED cycle heartbeats; a cycle that skips
    # on a live lock stays silent on purpose, so the window absorbs one full
    # grok run (GROK_TIMEOUT_SECS = 1h) plus bookkeeping. 90m.
    "grok-page-responder": {"open": 90 * _MIN, "closed": 90 * _MIN, "requires_ib": False},
    # portfolio-archive — portfolio_snapshots cold-archive oneshot
    # (scripts/archive_portfolio_snapshots.py via radon-portfolio-archive.timer
    # on the VPS, 06:52 UTC daily). 48h window mirrors
    # web/lib/serviceHealthWindows.ts and db-backup. Turso + local disk
    # (+ optional S3) — no IB dependency.
    "portfolio-archive": {"open": 48 * _HOUR, "closed": 48 * _HOUR, "requires_ib": False},
    # db-retention — daily keep-latest sweep for append-only scan tables
    # (scripts/db_retention_sweep.py via radon-db-retention.timer).
    "db-retention": {"open": 48 * _HOUR, "closed": 48 * _HOUR, "requires_ib": False},
    # media-backup — nightly B2 mirror of media.radon.run tree
    # (cloud/scripts/media_backup.py via radon-media-backup.timer,
    # 10:15 UTC). 48h window mirrors web/lib/serviceHealthWindows.ts and
    # db-backup. Local disk + B2 only — no IB dependency.
    "media-backup": {"open": 48 * _HOUR, "closed": 48 * _HOUR, "requires_ib": False},
    # Equibles — Equibles API only, no IB. Daily calendar-day timers
    # (09:30 / 10:00 UTC) use a uniform 26h window; weekly timers (Mon
    # 13F, Tue ATS, Sat COT) use the 8-day preset-rebalance precedent.
    "equibles-short-crowding": {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    "equibles-filing-forensics": {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},
    "equibles-13f": {"open": 8 * _DAY, "closed": 8 * _DAY, "requires_ib": False},
    "equibles-ats-venue-share": {"open": 8 * _DAY, "closed": 8 * _DAY, "requires_ib": False},
    "equibles-cot-positioning": {"open": 8 * _DAY, "closed": 8 * _DAY, "requires_ib": False},
    # event-odds — laptop launchd 3x weekday, holiday-aware. Windows
    # match catalysts (7h open / 4d closed). Polymarket only — no IB.
    "event-odds": {"open": 7 * _HOUR, "closed": 4 * _DAY, "requires_ib": False},
    # disk-cleanup — WEEKLY root-filesystem reclaim on the VPS
    # (cloud/scripts/disk_cleanup.py via radon-disk-cleanup.timer, Sun 03:20
    # UTC). Heartbeats ok/error every run. 8-day window uses the
    # preset-rebalance weekly precedent (cadence + timer jitter). Docker +
    # local disk + journald only — no IB dependency.
    "disk-cleanup": {"open": 8 * _DAY, "closed": 8 * _DAY, "requires_ib": False},
}


# Services whose OPEN window is measured from today's opening bell rather
# than wall-clock age (check.py's open-bell grace). These write only while
# the session is running, so at 09:31 their true age spans the overnight or
# weekend gap when they legitimately did not run — the grace is what lets
# them carry a TIGHT open window without false-paging every Monday.
#
# R-187: this used to be `BUCKETS["intraday"]`, which conflates two
# different questions — how often the WATCHDOG checks a service, and whether
# the service writes only during RTH. theta-harvester and strength-
# confirmation are hourly RTH writers on the daily CHECK cadence, so they
# need the grace without changing how often they are polled.
OPEN_BELL_GRACE_SERVICES: frozenset[str] = frozenset({
    "theta-harvester",
    "strength-confirmation",
    # Same shape: hourly, RTH-only, on the daily check cadence.
    "flow-refresh",
})


BUCKETS: dict[str, list[str]] = {
    "intraday": [
        "vcg-scan",
        "gex-scan",
        "breadth-scan",
        # The 15-min RTH driver behind cri/vcg/gex. The scans heartbeat their
        # own names; nothing observed the DRIVER until R-325.
        "data-refresh",
        "cri-scan",
        "orders-sync",
        "portfolio-sync",
        "skew",
        # RTH position-drift sensor (REL-001) — silent during RTH = the
        # reconcile spine is down, page like the other intraday writers.
        "position-reconcile",
        # 15m live vol-cone sample; silent during RTH means the tab has
        # silently fallen back to yesterday's close.
        "vol-cone-intraday",
        # 5m RTH TRIN sample (IB snapshot); silent during RTH means the
        # hourly bars and MA(10) stop forming.
        "trin",
    ],
    "continuous": [
        "newsfeed-scraper",
        "replica-watchdog",
        # R-158: this carried a 5-minute freshness window and appeared in NO
        # bucket, so only the auto-derived `error` bucket saw it and
        # `_check_error` fires solely on state == "error". A stopped or
        # disabled radon-nextjs-db-watchdog.timer left the last `ok` row
        # un-age-checked indefinitely, and a stopped timer is `inactive`
        # rather than `failed`, so units.py missed it too — the Turso-wedge
        # auto-restart off with an ok row on the board.
        "nextjs-db-read",
        # Around-the-clock 5-minute prober (R-325).
        "incident-watchdog",
        "fill-monitor",
        "journal-sync",
        # Always-on heartbeat (writes service_health every 60s cycle).
        "ib-watchdog",
        # Event-driven relay health (error on stale-tick escalation, ok on
        # recovery). The error bucket below catches the escalation; including
        # it in continuous lets the 24h staleness window flag a dead relay.
        "ib-realtime-relay",
        # Minute-cadence host sampler heartbeat — the 10-min staleness
        # window flags a dead sampler within one continuous cycle.
        "host-metrics",
        # Continuous journal gap SLI (5m) — error when missing_exec_id_count > 0.
        "journal-gap-sli",
        # 30s P1 auto-fix poller on the VPS. A stalled auto-fixer is silent
        # by nature, so the 90m staleness window is the only thing that
        # surfaces it (2026-08-14: 2h40m wedged, nothing paged).
        "grok-page-responder",
    ],
    "daily": [
        "cash-flow-sync",
        # Daily 07:00 UTC Chronos-2 backfill + calibration. Nothing observed
        # it: the unit wrote no row on any path, so a throwing backfill left
        # the forecast tables silently not advancing. R-402.
        "forecast-nightly",
        # Hourly RTH dark-pool flow driver. It always wrote its own row; it
        # was simply in neither catalog, so nothing aged it. R-412.
        "flow-refresh",
        # Weekday 21:45 UTC prod->demo market-analytics mirror (R-325).
        "demo-mirror",
        # R-159: the TWR builder (Tue..Sat 07:30 ET). Silent means a Flex
        # lockout, a throttle embargo or a dead timer, and /performance keeps
        # serving the last snapshot until someone eyeballs the page.
        "perf-twr",
        "flex-pull",
        # Daily 20:30 ET evening execution sweep (monitor daemon) — hourly
        # check surfaces a missed run within 1h of the 26h window expiring.
        "execution-sweep",
        "flex-token-check",
        "menthorq-session",
        "menthorq-login-probe",
        "cta-sync",
        # Same timer as cta-sync, so the same daily age check. Without it the
        # row carries a freshness window nothing evaluates and only _check_error
        # would ever see it.
        "menthorq-cta",
        # Once-per-day writers — hourly check surfaces a delay within 1h
        # of the window expiring.
        "llm-token-index",
        "margin-debt",
        # Daily 13:20 UTC FRED Z.1 household-leverage pull — hourly check
        # surfaces a missed run within 1h of the 26h window expiring.
        "hhlev",
        # Daily 03:10 UTC LLM provider frontier model list refresh — hourly
        # check surfaces a missed run within 1h of the 26h window expiring.
        "model-catalog",
        # Daily 22:30 UTC Treasury yield-curve pull — hourly check surfaces
        # a missed run within 1h of the 26h window expiring.
        "yield-curve",
        # Daily 22:40 UTC SPX dividend-yield breadth sweep — hourly check
        # surfaces a missed run within 1h of the 26h window expiring.
        "div-yield",
        # Tue..Sat 11:00 UTC FINRA HY bond breadth pull — hourly check
        # surfaces a missed run within 1h of the 120h window expiring.
        "hy-ad",
        # Daily 21:45 UTC IB HYG/SPX credit-spread pull — hourly check
        # surfaces a missed run within 1h of the 26h window expiring.
        "credit-spread",
        # Daily 21:55 UTC IB IEI/HYG/DXY ratio pull — hourly check surfaces
        # a missed run within 1h of the 26h window expiring.
        "iei-hyg",
        # Daily 02:15 UTC Cboe SPX/VIX1D straddle pull — hourly check
        # surfaces a missed run within 1h of the 26h window expiring.
        "straddle",
        # Daily 02:20 UTC Cboe COR1M/3M/6M/1Y pull — hourly check surfaces
        # a missed run within 1h of the 26h window expiring.
        "cor",
        # Daily 02:35 UTC VIX x COR3M 20d correlation derive (fifteen minutes
        # behind its radon-cor parent) — hourly check surfaces a missed run
        # within 1h of the 26h window expiring.
        "vixcor",
        # Daily 02:45 UTC Cboe VIX/VIX3M term-structure pull — hourly check
        # surfaces a missed run within 1h of the 26h window expiring.
        "vixts",
        # Daily 22:20 UTC IB daily-bar dispersion sweep (post-close, Yahoo
        # fallback) — hourly check surfaces a missed run within 1h of the 26h
        # window expiring.
        "dispersion",
        # Daily 22:10 UTC SPY 1M IV rank pull (post-close, IB primary with a
        # UW fallback) — hourly check surfaces a missed run within 1h of the
        # 26h window expiring.
        "ivrank",
        # Daily Mon-Fri 20:45 UTC UW vol-cone pull — hourly check surfaces a
        # missed run within 1h of the 26h window expiring.
        "vol-cone",
        # Daily 21:50 UTC SKEW 2D derive (post parent SKEW finalize) —
        # hourly check surfaces a missed run within 1h of the 26h window.
        "skew2d",
        # Hourly knowledge-base ingest — daily-bucket hourly check matches
        # its 26h staleness window (a failed hour never pages; a missed
        # day surfaces within 1h of the window expiring).
        "knowledge-ingest",

        "leap-scan",
        "garch-scan",
        "oi-changes",
        "catalysts",
        # Post-close BPI breadth scan (radon-bpi.timer Mon-Fri 21:30 UTC) —
        # hourly check surfaces a missed run within 1h of the 4d window.
        "bpi-scan",
        # Top-candidates scans fired by radon-signals-refresh.timer — hourly
        # check surfaces a dead timer within 1h of the 4d window (R-068).
        "theta-harvester",
        "strength-confirmation",
        "scanner",
        "discover",
        "flow-analysis",
        # Daily drift audit on the VPS — hourly check surfaces a missed
        # run within 1h of the 26h window expiring.
        "config-drift",
        # Nightly DB backup on the VPS — hourly check surfaces a missed
        # dump within 1h of the 48h window expiring.
        "db-backup",
        # Weekly preset rebalance (monitor daemon) — hourly check surfaces
        # a missed week within 1h of the 8-day window expiring.
        "preset-rebalance",
        # Daily journal reconcile (monitor daemon) — hourly check surfaces a
        # missed run within 1h of the 26h window expiring.
        "journal-reconcile",
        # Daily post-expiry journal sweep (monitor daemon) — same 26h window.
        "journal-expiry-sweep",
        # Nightly portfolio_snapshots cold-archive on the VPS — 48h window.
        "portfolio-archive",
        # Nightly keep-latest snapshot retention sweep on the VPS — 48h window.
        "db-retention",
        # Nightly media.radon.run tree backup to B2 — 48h window.
        "media-backup",
        # Weekly weekend disk cleanup on the VPS — hourly check surfaces a
        # missed sweep within 1h of the 8-day window expiring.
        "disk-cleanup",
        # Equibles daily + weekly + event-odds. Hourly check surfaces a
        # missed fire within 1h of the window expiring (preset-rebalance
        # weekly precedent).
        "equibles-short-crowding",
        "equibles-filing-forensics",
        "equibles-13f",
        "equibles-ats-venue-share",
        "equibles-cot-positioning",
        "event-odds",
    ],
    # Every scheduled service EXCEPT watchdog-alerts. Including
    # watchdog-alerts here would create a recursive alerting loop:
    # the row is in state=error whenever we've alerted on something
    # else, which would trigger another alert, etc.
    "error": [s for s in SCHEDULED_SERVICES.keys() if s != "watchdog-alerts"],
}


def freshness_window_for(service: str, market_state: str) -> int:
    """Seconds-of-staleness threshold for ``service`` under the given
    market state. Unknown services fall back to 1h (matches the TS
    default).
    """
    window = SCHEDULED_SERVICES.get(service)
    if window is None:
        return 1 * _HOUR
    return window["open"] if market_state == "open" else window["closed"]


def is_service_monitored(service: str) -> bool:
    """Whether the service is applicable to this host's active architecture."""
    if service == "replica-watchdog":
        return REPLICA_PATH.is_file()
    return True


def requires_ib(service: str) -> bool:
    """True iff ``service`` is in the IB-dependent set. Unknown services
    return False so we never silently suppress alerts on a writer we
    haven't classified — false negative is safer than a false silence
    when IB Gateway is the suspected root cause.
    """
    entry = SCHEDULED_SERVICES.get(service)
    if entry is None:
        return False
    return bool(entry.get("requires_ib", False))
