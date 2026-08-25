"""REL-066 / R-157, R-158, R-159 (all P1).

R-157: a STRANDED transition journal blanket-downgrades every `Result=signal`
unit failure in the last 24 hours to P3. `af734d83` keyed the `in_flight`
branch on nothing but `TRANSITION_JOURNAL_PATH.exists()`. R-057 already
established that an interrupted deploy leaves that journal on disk
indefinitely and cannot self-clear, and shipped
`TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS = 3600` in `external_probe.py` for
exactly that reason — `units.py` never got the same bound. While the journal
sits there (a state the fleet already alarms at P2), every OOM-kill,
`systemctl kill` or stop-timeout SIGKILL of any `radon-*` unit in the
previous 24 h is reclassified P3 digest instead of P1 page.

R-158: `nextjs-db-read` is in `SCHEDULED_SERVICES` with a comment asserting
it is "staleness-checked", but in NO bucket list — so only the auto-derived
`error` bucket sees it, and `_check_error` fires solely on `state == "error"`.
A stopped or disabled `radon-nextjs-db-watchdog.timer` leaves the last `ok`
row un-age-checked forever, and a stopped timer is `inactive` rather than
`failed`, so `units.py` misses it too.

R-159: `radon-perf-twr` writes no `service_health` row and always exits 0 —
`main()` computes `payload["status"]` but exits non-zero only under
`--check`, which the unit's ExecStart does not pass. `perf-twr` is in NEITHER
catalog. On a Flex 1025 lockout the job returns `degraded`, systemd records
success, no row is written, and `check.py` treats "no row" as dormant.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from watchdog import services as svc
from watchdog import units as units_mod


class TestStrandedJournalStopsExcusingKills:
    def test_a_stranded_journal_no_longer_downgrades_a_signal_kill(self, monkeypatch):
        now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
        unit = {
            "Result": "signal",
            "InactiveEnterTimestamp": (now - timedelta(minutes=30)).strftime(
                "%a %Y-%m-%d %H:%M:%S UTC"
            ),
        }
        stranded = {
            "in_flight": True,
            "journal_age_seconds": units_mod.TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS + 60,
            "marker_mtime": None,
        }
        assert units_mod._is_deploy_collateral(unit, stranded, now) is False

    def test_a_live_journal_still_excuses_a_signal_kill(self):
        now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
        unit = {
            "Result": "signal",
            "InactiveEnterTimestamp": (now - timedelta(minutes=30)).strftime(
                "%a %Y-%m-%d %H:%M:%S UTC"
            ),
        }
        live = {"in_flight": True, "journal_age_seconds": 120, "marker_mtime": None}
        assert units_mod._is_deploy_collateral(unit, live, now) is True

    def test_the_bound_matches_the_external_probe(self):
        from watchdog import external_probe

        assert (
            units_mod.TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS
            == external_probe.TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS
        )

    def test_deploy_state_reports_the_journal_age(self, monkeypatch, tmp_path):
        journal = tmp_path / "transition.json"
        journal.write_text("{}")
        monkeypatch.setattr(units_mod, "TRANSITION_JOURNAL_PATH", journal)
        state = units_mod._read_deploy_evidence()
        assert state["in_flight"] is True
        assert state["journal_age_seconds"] is not None


# The `error` bucket is auto-derived from every scheduled service, so it is
# never evidence that a service is STALENESS-checked. Only these three run
# `_check_stale`.
STALENESS_BUCKETS = ("intraday", "continuous", "daily")


def _staleness_checked() -> set[str]:
    return {name for bucket in STALENESS_BUCKETS for name in svc.BUCKETS[bucket]}


class TestEveryScheduledServiceIsInABucket:
    def test_no_scheduled_service_falls_through_to_the_error_bucket_only(self):
        orphans = sorted(set(svc.SCHEDULED_SERVICES) - _staleness_checked())
        # watchdog-alerts is the meta-row this service writes about itself;
        # age-checking it would be a recursive alerting loop.
        orphans = [name for name in orphans if name != "watchdog-alerts"]
        assert orphans == [], (
            "these carry a freshness window that nothing age-checks — only "
            f"`_check_error` sees them, and it fires on state == 'error': {orphans}"
        )

    def test_nextjs_db_read_is_staleness_checked(self):
        assert "nextjs-db-read" in _staleness_checked(), (
            "its comment claims it is staleness-checked, but a stopped "
            "radon-nextjs-db-watchdog.timer leaves the last ok row un-aged "
            "forever — and a stopped timer is `inactive`, not `failed`, so "
            "units.py misses it too"
        )

    def test_perf_twr_is_in_both_catalogs(self):
        assert "perf-twr" in svc.SCHEDULED_SERVICES
        assert "perf-twr" in _staleness_checked()
        windows = (REPO / "web" / "lib" / "serviceHealthWindows.ts").read_text()
        assert '"perf-twr"' in windows, "the web catalog does not expect a row"


class TestPerfTwrWritesAHealthRow:
    def test_the_builder_records_its_own_outcome(self):
        builder = (REPO / "scripts" / "perf_twr_builder.py").read_text()
        assert "record_service_health" in builder or "service_cycle" in builder, (
            "radon-perf-twr writes no service_health row and always exits 0, so "
            "a Flex 1025 lockout is a silent degraded payload"
        )

    # TEST_AUDIT T-129: this test used to monkeypatch `_record_perf_twr_health`
    # with a lambda and then assert on the lambda it installed — the REAL
    # writer (which swallows every exception) never ran, so a `pass` body or
    # a broken `db.writer` import stayed green while no row was written and
    # check.py read "no row" as dormant. Patch the writer it calls instead.
    def test_a_degraded_payload_records_error(self, monkeypatch):
        import db.writer as writer
        import perf_twr_builder as builder

        recorded: list[tuple] = []
        monkeypatch.setattr(
            writer,
            "record_service_health",
            lambda service, state, **kw: recorded.append((service, state, kw)),
        )
        builder._record_perf_twr_health("degraded", error={"message": "flex lockout"})
        assert recorded == [
            ("perf-twr", "error", {"error": {"message": "flex lockout"}}),
        ]

    def test_main_writes_the_error_row_for_a_degraded_build(self, monkeypatch, capsys):
        import db.writer as writer
        import perf_twr_builder as builder

        recorded: list[tuple] = []
        monkeypatch.setattr(
            writer,
            "record_service_health",
            lambda service, state, **kw: recorded.append((service, state, kw)),
        )
        monkeypatch.setattr(
            builder,
            "build_and_persist",
            lambda **kw: {
                "status": "degraded",
                "flows_status": "quarantined",
                "nav_source": "flex",
                "period_start": "2026-01-02",
                "period_end": "2026-08-22",
            },
        )
        monkeypatch.setattr(sys, "argv", ["perf_twr_builder.py"])
        builder.main()
        assert len(recorded) == 1
        service, state, kw = recorded[0]
        assert (service, state) == ("perf-twr", "error")
        assert kw["error"]["class"] == "degraded_build"
        assert "status=degraded" in kw["error"]["message"]
        assert capsys.readouterr().out == "", "stdout is reserved for the result JSON"

    def test_the_health_writer_maps_status_to_state(self):
        import perf_twr_builder as builder

        assert builder._perf_twr_state("ok") == "ok"
        assert builder._perf_twr_state("degraded") == "error"
        assert builder._perf_twr_state("stale") == "error"
