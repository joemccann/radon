"""Pin the flow-refresh shed vs real-fail contract across three layers.

2026-08-24 19:00Z: all three POSTs 502'd on subprocess capacity. The
oneshot exited 1 (NRestarts=0), systemd left ActiveState=failed, and
the unit watchdog re-paged P1 every cooldown until the next timer.

Layers:
  * wrapper — persistent 502/503 is SHED_EXIT=75, remapped to process
    exit 0 when every scan shed (already covered by
    test_run_flow_refresh_wrapper.py).
  * unit — SuccessExitStatus=75 so a leaked 75 is inactive, not failed.
  * watchdog — Type=oneshot Result=exit-code pages P1 once per
    InactiveEnterTimestamp; the same timestamp is P3 (next timer retries).
    Type=simple failed+exit-code stays P1 every cycle.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from watchdog import units

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts" / "run_flow_refresh.sh"
UNIT = ROOT / "cloud" / "services" / "radon-flow-refresh.service"
NOW = datetime(2026, 8, 24, 19, 30, tzinfo=timezone.utc)
TS = "Mon 2026-08-24 19:00:00 UTC"


def test_wrapper_and_unit_agree_on_shed_exit_75() -> None:
    wrapper = WRAPPER.read_text()
    unit = UNIT.read_text()
    assert "SHED_EXIT=75" in wrapper
    assert "SuccessExitStatus=75" in unit
    assert "Type=oneshot" in unit
    # Process-level remap so a host that has not yet installed the unit
    # file still exits 0 on an all-shed run.
    assert "exit 0" in wrapper
    assert 'exit 1' in wrapper


def test_watchdog_shed_latch_is_p3_real_fail_is_p1() -> None:
    failed = (
        "Result=exit-code\n"
        "NRestarts=0\n"
        "Id=radon-flow-refresh.service\n"
        "ActiveState=failed\n"
        "SubState=failed\n"
        "Type=oneshot\n"
        f"InactiveEnterTimestamp={TS}\n"
    )
    current = units.parse_show_output(failed)
    first = units.evaluate(current=current, previous={}, now=NOW)
    assert [o.severity for o in first] == ["P1"]

    latched = units.evaluate(
        current=current,
        previous={
            "radon-flow-refresh.service": {
                "nrestarts": 0,
                "auto_restart": False,
                "active_state": "failed",
                "inactive_enter": TS,
            }
        },
        now=NOW,
    )
    assert [o.severity for o in latched] == ["P3"]

    simple = units.parse_show_output(
        "Result=exit-code\n"
        "NRestarts=0\n"
        "Id=radon-api.service\n"
        "ActiveState=failed\n"
        "SubState=failed\n"
        "Type=simple\n"
        f"InactiveEnterTimestamp={TS}\n"
    )
    still_p1 = units.evaluate(
        current=simple,
        previous={
            "radon-api.service": {
                "nrestarts": 0,
                "auto_restart": False,
                "active_state": "failed",
                "inactive_enter": TS,
            }
        },
        now=NOW,
    )
    assert [o.severity for o in still_p1] == ["P1"]
