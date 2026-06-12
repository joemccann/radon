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
   delay surfaces within 1h of the 25h window expiring.
 * ``error`` — orthogonal sweep; flags any scheduled service in
   ``state == 'error'`` past 2 consecutive cycles regardless of
   staleness.
"""
from __future__ import annotations

from typing import TypedDict

_MIN = 60
_HOUR = 60 * _MIN
_DAY = 24 * _HOUR


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
    # Market-hours-only writers (gated by MonitorDaemon's
    # requires_market_hours=True). Mirrors web/lib/serviceHealthWindows.ts;
    # closed window absorbs the longest weekend gap so the banner doesn't
    # fire overnight. extended is folded into closed on the TS side for
    # the same reason — writers don't run in extended hours.
    "orders-sync":      {"open": 10 * _MIN, "closed": 3 * _DAY, "requires_ib": True},
    "portfolio-sync":   {"open": 10 * _MIN, "closed": 3 * _DAY, "requires_ib": True},
    "journal-sync":     {"open": 10 * _MIN, "closed": 3 * _DAY, "requires_ib": True},
    "cash-flow-sync":   {"open": 25 * _HOUR, "closed": 25 * _HOUR, "requires_ib": False},
    "fill-monitor":     {"open": 5 * _MIN, "closed": 3 * _DAY, "requires_ib": True},
    "exit-orders":      {"open": 5 * _MIN, "closed": 3 * _DAY, "requires_ib": True},
    "flex-token-check": {"open": 25 * _HOUR, "closed": 25 * _HOUR, "requires_ib": False},
    "cri-scan":         {"open": 35 * _MIN, "closed": 1 * _DAY, "requires_ib": True},
    "vcg-scan":         {"open": 15 * _MIN, "closed": 1 * _DAY, "requires_ib": True},
    "cta-sync":         {"open": 25 * _HOUR, "closed": 72 * _HOUR, "requires_ib": False},
    # Daily-cadence writers (mirror web/lib/serviceHealthWindows.ts):
    #  * llm-token-index — radon-llm-index.timer, once/UTC-day 06:30; pulls
    #    Artificial Analysis only, no IB. 25h window covers cadence + drift.
    #  * leap-scan       — radon-leap.timer, once daily 14:00 UTC + on-demand;
    #    UW-only. 26h open covers a weekend, 3d closed the long gap.
    #  * garch-scan      — on-demand dashboard refresh (+ optional timer, not
    #    yet shipped); UW-only. Same daily windows as leap-scan.
    "llm-token-index":  {"open": 25 * _HOUR, "closed": 25 * _HOUR, "requires_ib": False},
    "leap-scan":        {"open": 26 * _HOUR, "closed": 3 * _DAY, "requires_ib": False},
    "garch-scan":       {"open": 26 * _HOUR, "closed": 3 * _DAY, "requires_ib": False},
    # ib-watchdog polls FastAPI /health every 60s and heartbeats a row each
    # cycle; 5-min window catches a dead watchdog process within minutes.
    # It MONITORS IB but does not depend on IB being healthy to run, so
    # requires_ib=False (suppressing it during an IB outage would defeat it).
    "ib-watchdog":      {"open": 5 * _MIN, "closed": 5 * _MIN, "requires_ib": False},
    # ib-realtime-relay is an event-driven writer: the WS relay records a row
    # only when its bounded stale-tick ladder escalates (error) or ticks
    # resume (ok). 24h window mirrors web/lib/serviceHealthWindows.ts so a
    # silent-but-healthy relay isn't flagged stale. requires_ib=False is
    # load-bearing: the relay error IS the IB-data-plane-dead signal, so
    # grouping it under IB-outage suppression would mute the alert.
    "ib-realtime-relay": {"open": 24 * _HOUR, "closed": 24 * _HOUR, "requires_ib": False},
    # Event-driven writer — only records a row when it heals. Match
    # the 24h window from web/lib/serviceHealthWindows.ts so the dash
    # banner and the watchdog agree on what "stale" means here.
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
}


BUCKETS: dict[str, list[str]] = {
    "intraday": [
        "vcg-scan",
        "cri-scan",
        "orders-sync",
        "portfolio-sync",
    ],
    "continuous": [
        "newsfeed-scraper",
        "replica-watchdog",
        "fill-monitor",
        "exit-orders",
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
    ],
    "daily": [
        "cash-flow-sync",
        "flex-token-check",
        "cta-sync",
        # Once-per-day writers — hourly check surfaces a delay within 1h
        # of the window expiring.
        "llm-token-index",
        "leap-scan",
        "garch-scan",
        # Daily drift audit on the VPS — hourly check surfaces a missed
        # run within 1h of the 26h window expiring.
        "config-drift",
        # Nightly DB backup on the VPS — hourly check surfaces a missed
        # dump within 1h of the 48h window expiring.
        "db-backup",
        # Weekly preset rebalance (monitor daemon) — hourly check surfaces
        # a missed week within 1h of the 8-day window expiring.
        "preset-rebalance",
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
