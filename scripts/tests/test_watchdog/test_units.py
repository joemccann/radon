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


def _block(unit_id, active="active", sub="running", result="success", nrestarts=0):
    lines = [
        f"Result={result}",
        f"NRestarts={nrestarts}",
        f"Id={unit_id}",
        f"ActiveState={active}",
        f"SubState={sub}",
    ]
    if nrestarts is None:
        lines = [l for l in lines if not l.startswith("NRestarts")]
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

        monkeypatch.setattr(wired_grouping, "dispatch_with_grouping", fake_dispatch)

        rc = main(["--bucket", "continuous"])
        assert rc == 0
        assert any(o.service == "radon-relay.service" for o in captured["outcomes"])
        out = capsys.readouterr().out
        assert "radon-relay.service" in out

    def test_other_buckets_do_not_run_units_check(self, db_conn, monkeypatch):
        from watchdog.__main__ import main
        import scripts.watchdog.units as wired_units
        import scripts.watchdog.grouping as wired_grouping

        def fail(**kw):
            raise AssertionError("units check must not run outside continuous")

        monkeypatch.setattr(wired_units, "check_units", fail)
        monkeypatch.setattr(wired_grouping, "dispatch_with_grouping", lambda **kw: None)

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
