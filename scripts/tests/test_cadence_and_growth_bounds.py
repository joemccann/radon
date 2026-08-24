"""REL-069 tranche D — R-170, R-171, R-187.

Three bounds that do not match the thing they bound: a retry budget of 16 s
against a hold of up to 3600 s, a media tree with no pruner at all, and a
4-day staleness window on an HOURLY writer.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "scripts" / "run_signals_refresh.sh"
SERVICES = REPO / "cloud" / "services"


def _unit_value(unit: str, key: str) -> str:
    for line in (SERVICES / unit).read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    return ""


def _oncalendar(unit: str) -> list[str]:
    return [
        line.split("=", 1)[1]
        for line in (SERVICES / unit).read_text().splitlines()
        if line.startswith("OnCalendar=")
    ]


# --------------------------------------------------------------------------
# R-170 — a capacity shed is not a failure worth paging for
# --------------------------------------------------------------------------
class TestSignalsRefreshShedIsNotAPage:
    def test_the_timer_no_longer_collides_with_the_hour_long_scans(self):
        signals = " ".join(_oncalendar("radon-signals-refresh.timer"))
        # radon-garch (14:00 UTC) and radon-leap (10:00 ET) both land in the
        # 14:00 UTC minute in EDT and each hold a general-lane slot for up to
        # 3600 s, leaving one slot for two scans.
        assert ":00:00" not in signals, (
            "three units firing in the same minute against a 3-slot general "
            "lane is a deterministic shed, not bad luck"
        )
        for peer in ("radon-garch.timer", "radon-leap.timer"):
            for slot in _oncalendar(peer):
                assert slot.split()[-2:] != signals.split()[-2:] or ":00:00" not in slot

    def test_an_exhausted_slot_cap_exits_zero(self):
        src = WRAPPER.read_text()
        assert "SHED_EXIT" in src or "capacity" in src.lower(), src[:0]
        assert re.search(r"exit 0", src), (
            "RETRY_LIMIT(2) x RETRY_DELAY(8) = 16 s of waiting cannot clear a "
            "hold of up to 3600 s; the wrapper then refuses the direct "
            "fallback (correctly) and exits 1, paging P1 hourly for a "
            "condition no retry can fix"
        )

    def test_the_shed_path_is_named_and_logged(self):
        src = WRAPPER.read_text()
        assert "capacity" in src.lower()
        assert "next slot" in src.lower() or "next hour" in src.lower()

    def test_a_real_failure_still_exits_non_zero(self):
        src = WRAPPER.read_text()
        assert re.search(r"exit 1", src), "only the shed path is exempt"

    def test_the_retry_budget_is_still_bounded_by_the_scan_deadline(self):
        src = WRAPPER.read_text()
        assert "SCAN_DEADLINE" in src


# --------------------------------------------------------------------------
# R-171 — the media tree needs a pruner, and the sweep needs a bound
# --------------------------------------------------------------------------
class TestMediaTreeIsBounded:
    def test_a_pruner_exists(self):
        src = (REPO / "scripts" / "newsfeed" / "mediaPermissions.js").read_text()
        assert "pruneMediaTree" in src, (
            "no unlink, prune or tmpfiles.d anywhere for the media tree — "
            "every image ever scraped is retained forever as a re-encoded PNG"
        )

    def test_the_pruner_has_a_retention_horizon_and_a_file_cap(self):
        src = (REPO / "scripts" / "newsfeed" / "mediaPermissions.js").read_text()
        assert "MEDIA_RETENTION_DAYS" in src
        assert "MAX_MEDIA_FILES" in src

    def test_the_pruner_only_touches_media_extensions(self):
        src = (REPO / "scripts" / "newsfeed" / "mediaPermissions.js").read_text()
        body = src.split("export async function pruneMediaTree(")[1].split("\n}")[0]
        assert "isPublicMediaFile" in body

    def test_the_permission_sweep_is_not_run_every_cycle(self):
        src = (REPO / "scripts" / "newsfeed" / "push_media.js").read_text()
        assert "SWEEP_MIN_INTERVAL_MS" in src, (
            "readdir + stat over the whole tree every 2 minutes, at a cost "
            "growing linearly in a directory that only grows"
        )

    def test_the_push_runs_the_pruner(self):
        src = (REPO / "scripts" / "newsfeed" / "push_media.js").read_text()
        assert "pruneMediaTree" in src


# --------------------------------------------------------------------------
# R-187 — a 4-day window on an hourly writer is a 96x slack
# --------------------------------------------------------------------------
SIGNALS_SERVICES = ("theta-harvester", "strength-confirmation")


class TestSignalsFreshnessMatchesItsCadence:
    @pytest.mark.parametrize("service", SIGNALS_SERVICES)
    def test_the_open_window_is_hours_not_days(self, service):
        from watchdog import services as mod

        window = mod.SCHEDULED_SERVICES[service]["open"]
        assert window <= 6 * 3600, (
            f"{service} is written hourly but tolerated {window / 86400:.0f}d "
            "of silence during the session"
        )

    @pytest.mark.parametrize("service", SIGNALS_SERVICES)
    def test_the_closed_window_still_absorbs_the_weekend(self, service):
        from watchdog import services as mod

        assert mod.SCHEDULED_SERVICES[service]["closed"] >= 3 * 86400

    @pytest.mark.parametrize("service", SIGNALS_SERVICES)
    def test_the_open_bell_grace_applies(self, service):
        from watchdog import services as mod

        assert (
            service in mod.OPEN_BELL_GRACE_SERVICES
            or service in mod.BUCKETS["intraday"]
        ), (
            "without the grace, a tight open window false-pages every Monday "
            "at 09:31 because the last row is Friday's close"
        )
        check = (REPO / "scripts" / "watchdog" / "check.py").read_text()
        assert "OPEN_BELL_GRACE_SERVICES" in check

    @pytest.mark.parametrize("service", SIGNALS_SERVICES)
    def test_the_web_windows_agree_with_the_watchdog(self, service):
        src = (REPO / "web" / "lib" / "serviceHealthWindows.ts").read_text()
        line = next(l for l in src.splitlines() if l.strip().startswith(f'"{service}"'))
        assert "4 * DAY" not in line.split("extended")[0], line
        assert "RTH_ONLY_SERVICES" in src

    @pytest.mark.parametrize("service", SIGNALS_SERVICES)
    def test_the_web_side_measures_from_the_open(self, service):
        src = (REPO / "web" / "lib" / "serviceHealthWindows.ts").read_text()
        rth = src.split("const RTH_ONLY_SERVICES = new Set([")[1].split("]);")[0]
        assert f'"{service}"' in rth
