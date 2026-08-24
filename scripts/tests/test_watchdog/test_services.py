"""Contract tests for the Python service list.

The watchdog hard-codes the scheduled services in scripts/watchdog/services.py
to avoid parsing TS at runtime. This test cross-references the canonical
TS source (web/lib/serviceHealthWindows.ts) and asserts both lists agree,
so a future SOT drift between TS and Python is caught by CI.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_TS_FILE = _PROJECT_ROOT / "web" / "lib" / "serviceHealthWindows.ts"


def _ts_scheduled_services() -> set[str]:
    """Parse web/lib/serviceHealthWindows.ts and return the set of
    service names whose category === "scheduled".
    """
    text = _TS_FILE.read_text(encoding="utf-8")
    # Match: "service-name": { ... category: "scheduled" ... }
    pattern = re.compile(
        r'"([a-z][a-z0-9\-]*)"\s*:\s*\{[^}]*category:\s*"scheduled"',
        re.MULTILINE | re.DOTALL,
    )
    return set(pattern.findall(text))


def _ts_requires_ib() -> dict[str, bool]:
    """Parse web/lib/serviceHealthWindows.ts and return a mapping of
    service name → requires_ib flag. Only entries that declare
    ``requires_ib`` are returned; missing flags surface as a contract
    failure in the test below.
    """
    text = _TS_FILE.read_text(encoding="utf-8")
    # Match: "service-name": { ... requires_ib: true|false ... }
    pattern = re.compile(
        r'"([a-z][a-z0-9\-]*)"\s*:\s*\{[^}]*?requires_ib:\s*(true|false)',
        re.MULTILINE | re.DOTALL,
    )
    return {name: (flag == "true") for name, flag in pattern.findall(text)}


class TestServiceCatalogContract:
    def test_python_lists_every_scheduled_ts_service(self):
        from watchdog import services as svc_mod

        ts_set = _ts_scheduled_services()
        py_set = set(svc_mod.SCHEDULED_SERVICES.keys())
        missing_in_py = ts_set - py_set
        assert not missing_in_py, (
            f"TS has scheduled services not in Python list: {sorted(missing_in_py)}. "
            "Add them to scripts/watchdog/services.py SCHEDULED_SERVICES."
        )

    def test_python_does_not_track_non_scheduled_services(self):
        from watchdog import services as svc_mod

        ts_set = _ts_scheduled_services()
        py_set = set(svc_mod.SCHEDULED_SERVICES.keys())
        extra_in_py = py_set - ts_set
        assert not extra_in_py, (
            f"Python lists services that aren't 'scheduled' in TS: {sorted(extra_in_py)}."
        )

    def test_every_service_has_window_data(self):
        from watchdog import services as svc_mod

        for name, window in svc_mod.SCHEDULED_SERVICES.items():
            assert "open" in window and isinstance(window["open"], int), name
            assert "closed" in window and isinstance(window["closed"], int), name
            assert window["open"] > 0, name
            assert window["closed"] > 0, name

    def test_every_service_declares_requires_ib_explicitly(self):
        """Every entry MUST set ``requires_ib`` so alert-grouping logic
        knows whether the service depends on IB Gateway upstream.

        Defaulting silently would mask misclassifications; we want an
        explicit value the watchdog can trust.
        """
        from watchdog import services as svc_mod

        for name, window in svc_mod.SCHEDULED_SERVICES.items():
            assert "requires_ib" in window, (
                f"{name} is missing the requires_ib flag in SCHEDULED_SERVICES"
            )
            assert isinstance(window["requires_ib"], bool), (
                f"{name}.requires_ib must be a bool, got {type(window['requires_ib']).__name__}"
            )

    def test_requires_ib_true_set_matches_verified_writers(self):
        """The IB-dependent set is locked to what the writer source code
        actually does (verified by reading scripts/vcg_scan.py,
        scripts/cri_scan.py, scripts/ib_orders.py, scripts/ib_sync.py,
        scripts/monitor_daemon/handlers/{fill_monitor,journal_sync}.py).

        Drift here should fail loudly so we don't quietly group alerts
        on services that aren't actually IB-dependent.
        """
        from watchdog import services as svc_mod

        ib_dependent = {
            name for name, w in svc_mod.SCHEDULED_SERVICES.items()
            if w.get("requires_ib") is True
        }
        expected = {
            "vcg-scan",
            "cri-scan",
            "orders-sync",
            "portfolio-sync",
            "fill-monitor",
            # `exit-orders` was here until R-141 — ExitOrdersHandler is no
            # longer registered by create_daemon(), so it never writes a
            # heartbeat and must not be in either catalog.
            "journal-sync",
            # REL-001: PositionReconcileHandler connects to IB every cycle
            # (fetch_ib_positions via IBClient) — verified in
            # scripts/monitor_daemon/handlers/position_reconcile.py.
            "position-reconcile",
            # Evening after-hours fill sweep (REL-012) — pulls get_fills()
            # from IB Gateway once per trading evening.
            "execution-sweep",
            # TRIN 5m RTH sampler: the hourly series has no non-IB source
            # (scripts/fetch_trin.py sample_live connects via IBClient).
            "trin",
        }
        assert ib_dependent == expected, (
            f"requires_ib=true mismatch.\n"
            f"  expected: {sorted(expected)}\n"
            f"  actual:   {sorted(ib_dependent)}"
        )

    def test_non_ib_services_marked_false(self):
        """Services with no IB call path must be requires_ib=false."""
        from watchdog import services as svc_mod

        non_ib_expected = {
            "newsfeed-scraper",
            "replica-watchdog",
            "cash-flow-sync",
            "flex-token-check",
            "cta-sync",
            "watchdog-alerts",
        }
        for name in non_ib_expected:
            entry = svc_mod.SCHEDULED_SERVICES.get(name)
            assert entry is not None, f"{name} missing from SCHEDULED_SERVICES"
            assert entry["requires_ib"] is False, (
                f"{name} must be requires_ib=False (verified: no IB call path)"
            )

    def test_requires_ib_matches_ts_for_scheduled_services(self):
        """Every scheduled service named in TS must carry the SAME
        requires_ib flag as the Python entry. Drift between the two
        files would let alert-grouping logic diverge from UI behaviour.
        """
        from watchdog import services as svc_mod

        ts_requires = _ts_requires_ib()
        py_services = svc_mod.SCHEDULED_SERVICES

        # Each scheduled service in Python must have an IB flag in TS too.
        mismatches: list[str] = []
        for name, py_entry in py_services.items():
            if name not in ts_requires:
                mismatches.append(f"{name}: missing from TS requires_ib")
                continue
            if py_entry["requires_ib"] != ts_requires[name]:
                mismatches.append(
                    f"{name}: py={py_entry['requires_ib']} ts={ts_requires[name]}"
                )
        assert not mismatches, "TS/Python drift on requires_ib:\n  " + "\n  ".join(mismatches)

    def test_requires_ib_helper_returns_bool(self):
        """Public helper for the watchdog: ``requires_ib(service)``
        returns the flag for a known service and ``False`` for unknown
        names (safer default — don't suppress alerts on a service we
        haven't classified yet).
        """
        from watchdog import services as svc_mod

        assert svc_mod.requires_ib("vcg-scan") is True
        assert svc_mod.requires_ib("cri-scan") is True
        assert svc_mod.requires_ib("newsfeed-scraper") is False
        assert svc_mod.requires_ib("unknown-service-name") is False


class TestSignalsRefreshCoverage:
    """R-068: radon-signals-refresh.timer fires the theta-harvester and
    strength-confirmation scans autonomously (hourly, Mon-Fri 09:00-16:00
    ET), so both must be watchdog-tracked scheduled services — on-demand
    category excluded them from every bucket, and a dead timer froze the
    Top-candidates panel silently (unit inactive, not failed, so units.py
    missed it too).

    R-187: the uniform 4d windows this originally pinned were 96x an HOURLY
    cadence, so a timer dead on Monday morning went unreported until Thursday.
    The reason 4d was chosen — the wrapper skips outside market hours without
    heartbeating, so Monday and post-holiday mornings legitimately serve a
    ~66-90h-old row — is now handled where it belongs, by check.py's
    open-bell grace, which caps the effective age at how long the session has
    been open. `closed` still absorbs the whole weekend.
    """

    def test_signals_refresh_scans_are_scheduled_with_daily_coverage(self):
        from watchdog import services as svc_mod

        for name in ("theta-harvester", "strength-confirmation"):
            assert name in svc_mod.SCHEDULED_SERVICES, (
                f"{name} must be watchdog-tracked: the signals-refresh timer "
                "is its only autonomous caller and a dead timer is silent"
            )
            window = svc_mod.SCHEDULED_SERVICES[name]
            assert window["open"] == 3 * 3600, name
            assert window["closed"] == 4 * 24 * 3600, name
            assert window["requires_ib"] is False, name
            assert name in svc_mod.BUCKETS["daily"], (
                f"{name} needs the hourly daily-bucket check so a dead "
                "signals-refresh timer surfaces within 1h of the window"
            )
            assert name in svc_mod.OPEN_BELL_GRACE_SERVICES, (
                f"{name} writes only during RTH, so its tight open window "
                "must be measured from today's opening bell"
            )


class TestFlowRefreshCoverage:
    def test_flow_tabs_are_scheduled_with_daily_coverage(self):
        from watchdog import services as svc_mod

        for name in ("scanner", "discover", "flow-analysis"):
            assert name in svc_mod.SCHEDULED_SERVICES, name
            window = svc_mod.SCHEDULED_SERVICES[name]
            assert window["open"] == 4 * 24 * 3600, name
            assert window["closed"] == 4 * 24 * 3600, name
            assert window["requires_ib"] is False, name
            assert name in svc_mod.BUCKETS["daily"], name


class TestSkewCoverage:
    """radon-skew.timer fires every 5 minutes during RTH. The RTH window
    must span two cycles so one slow run never flips skew to stale."""

    def test_skew_open_window_spans_two_timer_cycles(self):
        from watchdog import services as svc_mod

        window = svc_mod.SCHEDULED_SERVICES["skew"]
        assert window["open"] == 10 * 60
        assert window["closed"] == 26 * 3600
        assert window["requires_ib"] is False
        assert "skew" in svc_mod.BUCKETS["intraday"]


class TestBuckets:
    def test_intraday_bucket_lists_market_hours_services(self):
        from watchdog import services as svc_mod

        intraday = set(svc_mod.BUCKETS["intraday"])
        assert "vcg-scan" in intraday
        assert "cri-scan" in intraday
        assert "orders-sync" in intraday
        assert "portfolio-sync" in intraday

    def test_continuous_bucket_lists_always_on_services(self):
        from watchdog import services as svc_mod

        cont = set(svc_mod.BUCKETS["continuous"])
        assert "newsfeed-scraper" in cont
        assert "replica-watchdog" in cont
        assert "fill-monitor" in cont
        assert "exit-orders" not in cont  # R-141: unscheduled, no heartbeat
        assert "journal-sync" in cont
        assert "journal-gap-sli" in cont

    def test_daily_bucket_lists_daily_services(self):
        from watchdog import services as svc_mod

        daily = set(svc_mod.BUCKETS["daily"])
        assert "cash-flow-sync" in daily
        assert "flex-token-check" in daily

    def test_error_bucket_lists_every_scheduled_service_except_self_meta(self):
        from watchdog import services as svc_mod

        # watchdog-alerts is the meta-row the watchdog writes to when
        # alerting on OTHER services. Including it in the error bucket
        # would create a recursive alerting loop (alert about an alert
        # row, which then triggers another alert, etc). Every other
        # scheduled service should be in the error bucket.
        error_bucket = set(svc_mod.BUCKETS["error"])
        expected = set(svc_mod.SCHEDULED_SERVICES.keys()) - {"watchdog-alerts"}
        assert error_bucket == expected
        assert "watchdog-alerts" not in error_bucket

    def test_no_unknown_buckets(self):
        from watchdog import services as svc_mod

        assert set(svc_mod.BUCKETS.keys()) == {"intraday", "continuous", "daily", "error"}
