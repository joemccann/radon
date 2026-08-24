"""Tests for the systemd-unit flap/failure watchdog (watchdog/units.py).

The check is ALERT-ONLY (feedback_ib_auto_recovery_conservative): it
never starts/stops/restarts a unit. It parses `systemctl show 'radon-*'`
output, compares against the persisted last-cycle snapshot, and returns
CheckOutcome objects that ride the continuous bucket's existing
dispatch path.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from watchdog import units


NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def _block(
    unit_id,
    active="active",
    sub="running",
    result="success",
    nrestarts=0,
    unit_type=None,
    inactive_enter=None,
):
    lines = [
        f"Result={result}",
        f"NRestarts={nrestarts}",
        f"Id={unit_id}",
        f"ActiveState={active}",
        f"SubState={sub}",
    ]
    if nrestarts is None:
        lines = [l for l in lines if not l.startswith("NRestarts")]
    if unit_type:
        lines.append(f"Type={unit_type}")
    if inactive_enter:
        lines.append(f"InactiveEnterTimestamp={inactive_enter}")
    return "\n".join(lines)


def _show_output(*blocks):
    return "\n\n".join(blocks) + "\n"


STEADY = _show_output(
    _block("radon-api.service", nrestarts=2),
    _block("radon-relay.service", nrestarts=0),
    _block("radon-refresh.timer", sub="waiting", nrestarts=None),
)


# ── parsing ──────────────────────────────────────────────────────────

class TestParseShowOutput:
    def test_parses_blocks_into_dicts(self):
        parsed = units.parse_show_output(STEADY)
        assert len(parsed) == 3
        api = next(u for u in parsed if u["Id"] == "radon-api.service")
        assert api["ActiveState"] == "active"
        assert api["SubState"] == "running"
        assert api["Result"] == "success"
        assert api["NRestarts"] == 2

    def test_timer_block_without_nrestarts(self):
        parsed = units.parse_show_output(STEADY)
        timer = next(u for u in parsed if u["Id"] == "radon-refresh.timer")
        assert timer["NRestarts"] is None
        assert timer["SubState"] == "waiting"

    def test_empty_output(self):
        assert units.parse_show_output("") == []
        assert units.parse_show_output("\n\n") == []

    def test_blocks_without_id_are_dropped(self):
        assert units.parse_show_output("Result=success\nActiveState=active\n") == []


# ── evaluation: failed state ─────────────────────────────────────────

class TestFailedState:
    def test_failed_unit_alerts_p1(self):
        current = units.parse_show_output(
            _show_output(_block("radon-relay.service", active="failed", sub="failed", result="exit-code", nrestarts=5))
        )
        outcomes = units.evaluate(current=current, previous={}, now=NOW)
        assert len(outcomes) == 1
        o = outcomes[0]
        assert o.service == "radon-relay.service"
        assert o.fired is True
        assert o.severity == "P1"
        assert "failed" in o.message
        assert "exit-code" in o.message

    def test_start_limit_hit_alert_calls_out_no_auto_recover(self):
        current = units.parse_show_output(
            _show_output(_block("radon-api.service", active="failed", sub="failed", result="start-limit-hit", nrestarts=5))
        )
        outcomes = units.evaluate(current=current, previous={}, now=NOW)
        assert len(outcomes) == 1
        o = outcomes[0]
        assert o.severity == "P1"
        assert "start-limit-hit" in o.message
        assert "auto-recover" in o.message.lower()

    def test_failed_fires_even_on_first_sight(self):
        """No previous-state requirement — failed (especially
        start-limit-hit) never self-heals, so cycle 1 must alert."""
        current = units.parse_show_output(
            _show_output(_block("radon-monitor.service", active="failed", sub="failed", result="start-limit-hit"))
        )
        outcomes = units.evaluate(current=current, previous={}, now=NOW)
        assert [o.fired for o in outcomes] == [True]


# ── evaluation: timer-owned oneshot exit-code latch ───────────────────

FLOW_FAIL_TS = "Mon 2026-08-24 19:00:00 UTC"
FLOW_FAIL_TS_NEXT = "Mon 2026-08-24 20:00:00 UTC"


def _oneshot_exit_code(unit_id="radon-flow-refresh.service", ts=FLOW_FAIL_TS):
    return _block(
        unit_id,
        active="failed",
        sub="failed",
        result="exit-code",
        nrestarts=0,
        unit_type="oneshot",
        inactive_enter=ts,
    )


class TestOneshotExitCodeLatch:
    """Type=oneshot + Result=exit-code + NRestarts=0 stays ActiveState=failed
    until the next timer fire. The 2026-08-24 19:00Z flow-refresh capacity
    shed left that latch in place; the continuous bucket re-paged P1 every
    cooldown for the same ExecStart. First sight of a timestamp is a real
    ExecStart failure (P1). Re-observing the same timestamp is not.
    """

    def test_oneshot_exit_code_pages_p1_on_first_sight(self):
        current = units.parse_show_output(_show_output(_oneshot_exit_code()))
        outcomes = units.evaluate(current=current, previous={}, now=NOW)
        assert len(outcomes) == 1
        assert outcomes[0].severity == "P1"
        assert outcomes[0].fired is True
        assert "exit-code" in outcomes[0].message

    def test_oneshot_exit_code_same_timestamp_is_p3(self):
        current = units.parse_show_output(_show_output(_oneshot_exit_code()))
        previous = {
            "radon-flow-refresh.service": {
                "nrestarts": 0,
                "auto_restart": False,
                "active_state": "failed",
                "inactive_enter": FLOW_FAIL_TS,
            }
        }
        outcomes = units.evaluate(current=current, previous=previous, now=NOW)
        assert len(outcomes) == 1
        assert outcomes[0].severity == "P3"
        assert outcomes[0].fired is True
        assert "next timer" in outcomes[0].message.lower()

    def test_oneshot_exit_code_new_timestamp_pages_p1_again(self):
        current = units.parse_show_output(
            _show_output(_oneshot_exit_code(ts=FLOW_FAIL_TS_NEXT))
        )
        previous = {
            "radon-flow-refresh.service": {
                "nrestarts": 0,
                "auto_restart": False,
                "active_state": "failed",
                "inactive_enter": FLOW_FAIL_TS,
            }
        }
        outcomes = units.evaluate(current=current, previous=previous, now=NOW)
        assert [o.severity for o in outcomes] == ["P1"]

    def test_simple_service_exit_code_stays_p1_every_cycle(self):
        """Do not weaken Restart= services: failed+exit-code stays P1."""
        current = units.parse_show_output(
            _show_output(
                _block(
                    "radon-api.service",
                    active="failed",
                    sub="failed",
                    result="exit-code",
                    nrestarts=0,
                    unit_type="simple",
                    inactive_enter=FLOW_FAIL_TS,
                )
            )
        )
        previous = {
            "radon-api.service": {
                "nrestarts": 0,
                "auto_restart": False,
                "active_state": "failed",
                "inactive_enter": FLOW_FAIL_TS,
            }
        }
        outcomes = units.evaluate(current=current, previous=previous, now=NOW)
        assert [o.severity for o in outcomes] == ["P1"]

    def test_oneshot_start_limit_hit_stays_p1(self):
        current = units.parse_show_output(
            _show_output(
                _block(
                    "radon-flow-refresh.service",
                    active="failed",
                    sub="failed",
                    result="start-limit-hit",
                    nrestarts=0,
                    unit_type="oneshot",
                    inactive_enter=FLOW_FAIL_TS,
                )
            )
        )
        previous = {
            "radon-flow-refresh.service": {
                "nrestarts": 0,
                "auto_restart": False,
                "active_state": "failed",
                "inactive_enter": FLOW_FAIL_TS,
            }
        }
        outcomes = units.evaluate(current=current, previous=previous, now=NOW)
        assert [o.severity for o in outcomes] == ["P1"]
        assert "start-limit-hit" in outcomes[0].message


# ── evaluation: flap detection ───────────────────────────────────────

class TestFlapDetection:
    def test_single_cycle_auto_restart_does_not_alert(self):
        current = units.parse_show_output(
            _show_output(_block("radon-nextjs.service", active="activating", sub="auto-restart", result="exit-code", nrestarts=10))
        )
        outcomes = units.evaluate(current=current, previous={}, now=NOW)
        assert outcomes == []

    def test_two_consecutive_cycles_auto_restart_alerts(self):
        previous = {
            "radon-nextjs.service": {"nrestarts": 10, "auto_restart": True, "active_state": "activating"},
        }
        current = units.parse_show_output(
            _show_output(_block("radon-nextjs.service", active="activating", sub="auto-restart", result="exit-code", nrestarts=70))
        )
        outcomes = units.evaluate(current=current, previous=previous, now=NOW)
        assert len(outcomes) == 1
        o = outcomes[0]
        assert o.fired is True
        assert o.severity == "P1"
        assert "crash-loop" in o.message

    def test_recovered_after_one_flap_cycle_no_alert(self):
        previous = {
            "radon-nextjs.service": {"nrestarts": 10, "auto_restart": True, "active_state": "activating"},
        }
        current = units.parse_show_output(
            _show_output(_block("radon-nextjs.service", active="active", sub="running", nrestarts=11))
        )
        outcomes = units.evaluate(current=current, previous=previous, now=NOW)
        # NRestarts +1 since last cycle still surfaces as the P3 delta signal.
        assert all(o.severity != "P1" for o in outcomes)

    def test_recovered_p1_condition_emits_a_healthy_observation(self):
        previous = {
            "radon-nextjs.service": {
                "nrestarts": 10,
                "auto_restart": True,
                "active_state": "activating",
            },
        }
        current = units.parse_show_output(
            _show_output(
                _block(
                    "radon-nextjs.service",
                    active="active",
                    sub="running",
                    nrestarts=11,
                )
            )
        )

        outcomes = units.evaluate(current=current, previous=previous, now=NOW)

        recovered = [o for o in outcomes if o.status == "healthy"]
        assert len(recovered) == 1
        assert recovered[0].service == "radon-nextjs.service"
        assert recovered[0].fired is False


# ── evaluation: NRestarts delta ──────────────────────────────────────

class TestNRestartsDelta:
    def test_delta_alerts_p3(self):
        previous = {"radon-api.service": {"nrestarts": 3, "auto_restart": False, "active_state": "active"}}
        current = units.parse_show_output(
            _show_output(_block("radon-api.service", nrestarts=7))
        )
        outcomes = units.evaluate(current=current, previous=previous, now=NOW)
        assert len(outcomes) == 1
        o = outcomes[0]
        assert o.severity == "P3"
        assert o.fired is True
        assert "+4" in o.message

    def test_equal_nrestarts_no_alert(self):
        previous = {"radon-api.service": {"nrestarts": 3, "auto_restart": False, "active_state": "active"}}
        current = units.parse_show_output(_show_output(_block("radon-api.service", nrestarts=3)))
        assert units.evaluate(current=current, previous=previous, now=NOW) == []

    def test_counter_reset_after_manual_restart_no_alert(self):
        """systemctl restart resets NRestarts to 0 — a decrease is a
        deploy artifact, not a crash."""
        previous = {"radon-api.service": {"nrestarts": 9, "auto_restart": False, "active_state": "active"}}
        current = units.parse_show_output(_show_output(_block("radon-api.service", nrestarts=0)))
        assert units.evaluate(current=current, previous=previous, now=NOW) == []

    def test_failed_takes_priority_over_delta(self):
        """One alert per unit per cycle — highest severity wins."""
        previous = {"radon-api.service": {"nrestarts": 1, "auto_restart": False, "active_state": "active"}}
        current = units.parse_show_output(
            _show_output(_block("radon-api.service", active="failed", sub="failed", result="start-limit-hit", nrestarts=6))
        )
        outcomes = units.evaluate(current=current, previous=previous, now=NOW)
        assert len(outcomes) == 1
        assert outcomes[0].severity == "P1"
        assert "start-limit-hit" in outcomes[0].message


# ── evaluation: steady state ─────────────────────────────────────────

class TestSteadyState:
    def test_no_alerts_when_everything_healthy(self):
        previous = {
            "radon-api.service": {"nrestarts": 2, "auto_restart": False, "active_state": "active"},
            "radon-relay.service": {"nrestarts": 0, "auto_restart": False, "active_state": "active"},
            "radon-refresh.timer": {"nrestarts": None, "auto_restart": False, "active_state": "active"},
        }
        current = units.parse_show_output(STEADY)
        assert units.evaluate(current=current, previous=previous, now=NOW) == []

    def test_first_ever_cycle_no_alerts(self):
        current = units.parse_show_output(STEADY)
        assert units.evaluate(current=current, previous={}, now=NOW) == []


# ── check_units: subprocess seam + state persistence ─────────────────

class TestCheckUnits:
    def test_runs_systemctl_persists_state_and_returns_alerts(self, tmp_path, monkeypatch):
        state_path = tmp_path / "units_state.json"
        failed = _show_output(
            _block("radon-api.service", nrestarts=2),
            _block("radon-relay.service", active="failed", sub="failed", result="start-limit-hit", nrestarts=5),
        )
        outcomes = units.check_units(now=NOW, state_path=state_path, show_runner=lambda: failed)
        assert [o.service for o in outcomes] == ["radon-relay.service"]
        assert outcomes[0].fired is True

        state = json.loads(state_path.read_text())
        assert state["units"]["radon-api.service"]["nrestarts"] == 2
        assert state["units"]["radon-relay.service"]["nrestarts"] == 5

    def test_flap_detection_across_two_real_cycles(self, tmp_path):
        state_path = tmp_path / "units_state.json"
        flapping = _show_output(
            _block("radon-nextjs.service", active="activating", sub="auto-restart", result="exit-code", nrestarts=10)
        )
        first = units.check_units(now=NOW, state_path=state_path, show_runner=lambda: flapping)
        assert first == []
        flapping_later = _show_output(
            _block("radon-nextjs.service", active="activating", sub="auto-restart", result="exit-code", nrestarts=70)
        )
        second = units.check_units(now=NOW, state_path=state_path, show_runner=lambda: flapping_later)
        assert len(second) == 1
        assert second[0].severity == "P1"

    def test_no_systemctl_degrades_to_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(units.shutil, "which", lambda _: None)
        outcomes = units.check_units(now=NOW, state_path=tmp_path / "s.json")
        assert outcomes == []
        assert not (tmp_path / "s.json").exists()

    def test_show_runner_failure_degrades_to_empty(self, tmp_path):
        def boom():
            raise RuntimeError("systemctl exploded")

        outcomes = units.check_units(now=NOW, state_path=tmp_path / "s.json", show_runner=boom)
        assert outcomes == []

    def test_corrupt_state_file_is_tolerated(self, tmp_path):
        state_path = tmp_path / "units_state.json"
        state_path.write_text("{not json")
        outcomes = units.check_units(now=NOW, state_path=state_path, show_runner=lambda: STEADY)
        assert outcomes == []
        assert json.loads(state_path.read_text())["units"]


# ── __main__ wiring: continuous bucket only, alert-only dispatch ────

class TestContinuousBucketWiring:
    def test_continuous_bucket_dispatches_unit_outcomes(self, db_conn, monkeypatch, capsys):
        from watchdog.__main__ import main
        import scripts.watchdog.units as wired_units
        import scripts.watchdog.grouping as wired_grouping

        unit_outcome = units._outcome_for(
            unit_id="radon-relay.service",
            severity="P1",
            message="systemd unit failed (Result=start-limit-hit)",
            now=NOW,
        )
        monkeypatch.setattr(wired_units, "check_units", lambda **kw: [unit_outcome])

        captured = {}

        def fake_dispatch(*, outcomes, now):
            captured["outcomes"] = list(outcomes)
            return wired_grouping.DispatchSummary()

        monkeypatch.setattr(wired_grouping, "dispatch_with_grouping", fake_dispatch)

        rc = main(["--bucket", "continuous"])
        assert rc == 0
        assert any(o.service == "radon-relay.service" for o in captured["outcomes"])
        out = capsys.readouterr().out
        assert "radon-relay.service" in out

    def test_continuous_bucket_cancels_unit_emergency_on_recovery(
        self, db_conn, monkeypatch
    ):
        from watchdog.__main__ import main
        from watchdog.check import CheckOutcome
        from watchdog import cooldown as wired_cooldown
        from watchdog import notify as wired_notify
        import scripts.watchdog.units as wired_units
        import scripts.watchdog.grouping as wired_grouping
        import scripts.watchdog.external_probe as wired_external_probe

        recovered = CheckOutcome(
            service="radon-relay.service",
            kind="unit",
            status="healthy",
            severity=None,
            fired=False,
            message="systemd unit recovered",
            consecutive_failures=0,
            now=NOW,
        )
        monkeypatch.setattr(wired_units, "check_units", lambda **kw: [recovered])
        monkeypatch.setattr(
            wired_external_probe,
            "check_external_probe",
            lambda **kw: CheckOutcome(
                service="external-health-probe",
                kind="deadman",
                status="healthy",
                severity=None,
                fired=False,
                message="off-box observer current",
                consecutive_failures=0,
                now=NOW,
            ),
        )
        monkeypatch.setattr(
            wired_grouping,
            "dispatch_with_grouping",
            lambda **kw: wired_grouping.DispatchSummary(),
        )
        monkeypatch.setattr(
            wired_cooldown,
            "active_emergency_services",
            lambda **kw: ["radon-relay.service"],
        )
        cancelled = []
        resolved = []
        monkeypatch.setattr(wired_notify, "cancel_emergency", cancelled.append)
        monkeypatch.setattr(
            wired_cooldown,
            "mark_emergency_resolved",
            lambda **kw: resolved.append(kw["service"]),
        )

        assert main(["--bucket", "continuous"]) == 0

        assert cancelled == ["radon-relay.service"]
        assert resolved == ["radon-relay.service"]

    def test_other_buckets_do_not_run_units_check(self, db_conn, monkeypatch):
        from watchdog.__main__ import main
        import scripts.watchdog.units as wired_units
        import scripts.watchdog.grouping as wired_grouping

        def fail(**kw):
            raise AssertionError("units check must not run outside continuous")

        monkeypatch.setattr(wired_units, "check_units", fail)
        monkeypatch.setattr(
            wired_grouping,
            "dispatch_with_grouping",
            lambda **kw: wired_grouping.DispatchSummary(),
        )

        rc = main(["--bucket", "daily"])
        assert rc == 0

    def test_units_module_never_calls_systemctl_mutators(self):
        """Alert-only contract: the module's single subprocess call must
        be the read-only `systemctl show` probe — no state-changing verb
        ever reaches subprocess (operator-hint TEXT in alert messages is
        fine; invocations are not)."""
        source = Path(units.__file__).read_text()
        assert source.count("subprocess.run") == 1
        assert '["systemctl", "show", UNIT_GLOB' in source


# ── evaluation: deploy-collateral signal kills ───────────────────────

class TestDeployCollateralSignalKill:
    """A deploy's stop-clean SIGTERMs in-flight oneshots (radon-bpi
    2026-08-05 21:40:24Z, killed the same second as radon-deploy-root
    stop-clean; 2026-08-14 22:52:36Z, first of three stacked deploys,
    last green 34 min later). That is Result=signal collateral, not an
    outage — it must ride the P3 digest, not page P1. Everything else
    about failed units is unchanged: exit-code failures, start-limit-hit,
    and signal kills with no deploy evidence still page."""

    WINDOW_NOW = datetime(2026, 8, 5, 21, 50, tzinfo=timezone.utc)

    @staticmethod
    def _ts(dt: datetime) -> str:
        return dt.strftime("%a %Y-%m-%d %H:%M:%S UTC")

    def _signal_block(self, killed_at: datetime, result: str = "signal") -> str:
        return _show_output(
            _block("radon-bpi.service", active="failed", sub="failed",
                   result=result, nrestarts=0)
            + f"\nInactiveEnterTimestamp={self._ts(killed_at)}"
        )

    def test_signal_kill_before_green_marker_downgrades_to_p3(self):
        killed = datetime(2026, 8, 5, 21, 40, 24, tzinfo=timezone.utc)
        marker = datetime(2026, 8, 5, 21, 41, 0, tzinfo=timezone.utc)
        current = units.parse_show_output(self._signal_block(killed))
        outcomes = units.evaluate(
            current=current, previous={}, now=self.WINDOW_NOW,
            deploy={"marker_mtime": marker, "in_flight": False},
        )
        assert len(outcomes) == 1
        assert outcomes[0].severity == "P3"
        assert "deploy" in outcomes[0].message.lower()

    def test_signal_kill_during_inflight_deploy_downgrades_to_p3(self):
        killed = self.WINDOW_NOW.replace(minute=48)
        current = units.parse_show_output(self._signal_block(killed))
        outcomes = units.evaluate(
            current=current, previous={}, now=self.WINDOW_NOW,
            # REL-066 / R-157: `in_flight` alone is no longer enough. An
            # interrupted deploy leaves the transition journal on disk
            # indefinitely and it cannot self-clear, so an unbounded
            # `in_flight` downgraded every signal kill in the previous 24h to
            # P3 digest. A FRESH journal is still deploy evidence.
            deploy={"marker_mtime": None, "in_flight": True, "journal_age_seconds": 120},
        )
        assert [o.severity for o in outcomes] == ["P3"]

    def test_signal_kill_during_a_STRANDED_journal_stays_p1(self):
        killed = self.WINDOW_NOW.replace(minute=48)
        current = units.parse_show_output(self._signal_block(killed))
        outcomes = units.evaluate(
            current=current, previous={}, now=self.WINDOW_NOW,
            deploy={
                "marker_mtime": None,
                "in_flight": True,
                "journal_age_seconds": units.TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS + 1,
            },
        )
        assert [o.severity for o in outcomes] == ["P1"]

    def test_signal_kill_without_deploy_evidence_stays_p1(self):
        killed = datetime(2026, 8, 5, 21, 40, 24, tzinfo=timezone.utc)
        current = units.parse_show_output(self._signal_block(killed))
        outcomes = units.evaluate(
            current=current, previous={}, now=self.WINDOW_NOW,
            deploy={"marker_mtime": None, "in_flight": False},
        )
        assert [o.severity for o in outcomes] == ["P1"]

    def test_signal_kill_far_past_oneshot_horizon_stays_p1(self):
        """A signal kill whose latest post-kill green (or age) sits past
        the 24h oneshot recovery horizon is no longer deploy collateral."""
        killed = datetime(2026, 8, 4, 18, 0, 0, tzinfo=timezone.utc)
        marker = datetime(2026, 8, 5, 21, 41, 0, tzinfo=timezone.utc)
        current = units.parse_show_output(self._signal_block(killed))
        outcomes = units.evaluate(
            current=current, previous={}, now=self.WINDOW_NOW,
            deploy={"marker_mtime": marker, "in_flight": False},
        )
        assert [o.severity for o in outcomes] == ["P1"]

    def test_stacked_deploy_signal_kill_34min_before_green_is_p3(self):
        """2026-08-14 23:30Z page: first of three deploys stop-cleaned
        radon-bpi at 22:52:36Z; the last one greened at 23:27:11Z.
        34 min exceeds the old 20-min single-deploy budget, but it is
        still stop-clean collateral — P3, not a P1 page."""
        killed = datetime(2026, 8, 14, 22, 52, 36, tzinfo=timezone.utc)
        marker = datetime(2026, 8, 14, 23, 27, 11, tzinfo=timezone.utc)
        now = datetime(2026, 8, 14, 23, 30, 1, tzinfo=timezone.utc)
        current = units.parse_show_output(self._signal_block(killed))
        outcomes = units.evaluate(
            current=current, previous={}, now=now,
            deploy={"marker_mtime": marker, "in_flight": False},
        )
        assert len(outcomes) == 1
        assert outcomes[0].severity == "P3"
        assert "deploy" in outcomes[0].message.lower()

    def test_signal_kill_after_last_green_during_cancelled_stack_is_p3(self):
        """Between stacked deploys the journal is gone and the last
        green marker is still the previous release (20:25Z). The 22:52
        SIGTERM is still the cancelled deploy's stop-clean."""
        killed = datetime(2026, 8, 14, 22, 52, 36, tzinfo=timezone.utc)
        marker = datetime(2026, 8, 14, 20, 25, 0, tzinfo=timezone.utc)
        now = datetime(2026, 8, 14, 23, 20, 0, tzinfo=timezone.utc)
        current = units.parse_show_output(self._signal_block(killed))
        outcomes = units.evaluate(
            current=current, previous={}, now=now,
            deploy={"marker_mtime": marker, "in_flight": False},
        )
        assert [o.severity for o in outcomes] == ["P3"]

    def test_oneshot_still_failed_61min_after_stop_clean_is_p3(self):
        """2026-08-15 01:35Z page: stop-clean killed radon-bpi at
        00:34:41Z; green marker 00:35:59Z (78s later). Type=oneshot
        stays failed until the next timer (Sat 11:00 UTC). Watchdog
        cycle at 01:35:00Z is 3619s after the kill, 19s past the
        now-to-kill age cap, so P3 flipped to P1 and paged. Kill-to-
        marker is 78s — still stop-clean collateral."""
        killed = datetime(2026, 8, 15, 0, 34, 41, tzinfo=timezone.utc)
        marker = datetime(2026, 8, 15, 0, 35, 59, tzinfo=timezone.utc)
        now = datetime(2026, 8, 15, 1, 35, 0, tzinfo=timezone.utc)
        current = units.parse_show_output(self._signal_block(killed))
        outcomes = units.evaluate(
            current=current, previous={}, now=now,
            deploy={"marker_mtime": marker, "in_flight": False},
        )
        assert len(outcomes) == 1
        assert outcomes[0].severity == "P3"
        assert "deploy" in outcomes[0].message.lower()

    def test_stacked_successor_green_158min_after_kill_is_p3(self):
        """2026-08-20 02:45Z page b76d4a52: a231 stop-cleaned radon-bpi
        at 00:04:23Z (green 00:05:39Z, kill-to-marker 76s → P3). Four
        more deploys stacked; 0f7d8e5f greened at 02:42:18Z and
        overwrote the marker. Kill-to-latest-marker is 9475s (>60 min),
        so the old kill-before-green bound flipped P3 to P1 even though
        the unit never recovered and edge /health/lite stayed up."""
        killed = datetime(2026, 8, 20, 0, 4, 23, tzinfo=timezone.utc)
        marker = datetime(2026, 8, 20, 2, 42, 18, tzinfo=timezone.utc)
        now = datetime(2026, 8, 20, 2, 45, 0, tzinfo=timezone.utc)
        current = units.parse_show_output(self._signal_block(killed))
        outcomes = units.evaluate(
            current=current, previous={}, now=now,
            deploy={"marker_mtime": marker, "in_flight": False},
        )
        assert len(outcomes) == 1
        assert outcomes[0].severity == "P3"
        assert "deploy" in outcomes[0].message.lower()

    def test_signal_kill_after_green_past_now_window_stays_p1(self):
        """Kill after last green is cancelled-stack collateral only
        while the kill itself is still inside the now-window. A
        oneshot failed >60 min after that kill, with no newer green,
        pages."""
        killed = datetime(2026, 8, 15, 2, 0, 0, tzinfo=timezone.utc)
        marker = datetime(2026, 8, 15, 0, 35, 59, tzinfo=timezone.utc)
        now = datetime(2026, 8, 15, 3, 5, 0, tzinfo=timezone.utc)
        current = units.parse_show_output(self._signal_block(killed))
        outcomes = units.evaluate(
            current=current, previous={}, now=now,
            deploy={"marker_mtime": marker, "in_flight": False},
        )
        assert [o.severity for o in outcomes] == ["P1"]

    def test_exit_code_failure_near_deploy_stays_p1(self):
        killed = datetime(2026, 8, 5, 21, 40, 24, tzinfo=timezone.utc)
        marker = datetime(2026, 8, 5, 21, 41, 0, tzinfo=timezone.utc)
        current = units.parse_show_output(self._signal_block(killed, result="exit-code"))
        outcomes = units.evaluate(
            current=current, previous={}, now=self.WINDOW_NOW,
            deploy={"marker_mtime": marker, "in_flight": False},
        )
        assert [o.severity for o in outcomes] == ["P1"]

    def test_evaluate_without_deploy_arg_is_unchanged(self):
        current = units.parse_show_output(
            _show_output(_block("radon-relay.service", active="failed",
                                sub="failed", result="signal"))
        )
        outcomes = units.evaluate(current=current, previous={}, now=NOW)
        assert [o.severity for o in outcomes] == ["P1"]

    def test_parse_systemd_timestamp(self):
        parsed = units._parse_systemd_timestamp("Wed 2026-08-05 21:40:24 UTC")
        assert parsed == datetime(2026, 8, 5, 21, 40, 24, tzinfo=timezone.utc)
        assert units._parse_systemd_timestamp("") is None
        assert units._parse_systemd_timestamp("n/a") is None
