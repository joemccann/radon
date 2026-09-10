"""data_refresh must not fail the systemd unit on a graceful scan degradation.

A scan that soft-fails (timeout / bad output) keeps the existing JSON and retries
on the next 15-min run — that's transient, self-healing degradation, not a process
crash. Exiting non-zero marks the unit 'failed' and pages the operator for noise
(cri_scan timed out once post-close 2026-06-15, fine on every neighbouring run).

Soft-fail MUST still heartbeat the child service (cri-scan / vcg-scan / gex-scan).
cri_scan only heartbeats at successful completion; when the parent kills it at the
budget the row goes silent and the 35m open window pages stale (2026-08-28
15:15Z, silent 43m after two 120s timeouts at 14:45/15:00 while neighbouring
runs finished in 63-103s).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import data_refresh as dr  # noqa: E402


def test_main_returns_0_on_full_success():
    with patch.object(dr, "_is_trading_day", return_value=True), \
         patch.object(dr, "_run_scan", side_effect=[True, True, True]):
        assert dr.main() == 0


def test_main_returns_0_when_a_scan_soft_fails():
    # cri soft-fails (e.g. budget timeout), vcg + gex ok — the unit must still succeed.
    with patch.object(dr, "_is_trading_day", return_value=True), \
         patch.object(dr, "_run_scan", side_effect=[False, True, True]):
        assert dr.main() == 0


def test_main_returns_0_when_all_scans_soft_fail():
    with patch.object(dr, "_is_trading_day", return_value=True), \
         patch.object(dr, "_run_scan", side_effect=[False, False, False]):
        assert dr.main() == 0


def test_main_refreshes_gex_without_menthorq():
    # GEX must be on the same cadence as cri/vcg (it had no scheduler of its own),
    # invoked with --no-mq so the durable UW snapshot lands inside the budget.
    with patch.object(dr, "_is_trading_day", return_value=True), \
         patch.object(dr, "_run_scan", return_value=True) as run:
        assert dr.main() == 0
        scripts_run = [call.args[0] for call in run.call_args_list]
        assert "gex_scan.py" in scripts_run
        gex_call = next(c for c in run.call_args_list if c.args[0] == "gex_scan.py")
        assert "--no-mq" in gex_call.args[1]


def test_main_returns_0_on_non_trading_day():
    with patch.object(dr, "_is_trading_day", return_value=False), \
         patch.object(dr, "_run_scan") as run:
        assert dr.main() == 0
        run.assert_not_called()


def test_cri_scan_budget_exceeds_observed_slow_path():
    # 2026-08-28: cri finished in 63-103s on healthy cycles and hit the old
    # 120s ceiling twice; budget must clear the observed slow path with slack.
    assert dr.CRI_SCAN_TIMEOUT_SECS >= 180
    assert dr.CRI_SCAN_TIMEOUT_SECS + 2 * dr.DEFAULT_SCAN_TIMEOUT_SECS <= 480


def test_run_scan_timeout_heartbeats_cri_scan_error(tmp_path, monkeypatch):
    """Parent-killed cri_scan must not leave service_health[cri-scan] silent."""
    out = tmp_path / "cri.json"
    out.write_text("{}")
    calls: list[tuple] = []

    def fake_health(service, state, **kwargs):
        calls.append((service, state, kwargs))

    monkeypatch.setattr(dr, "_PROJECT_DIR", tmp_path)
    monkeypatch.setattr(dr, "_SCRIPTS_DIR", tmp_path)
    (tmp_path / "cri_scan.py").write_text("# stub\n")

    with patch.object(dr.subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd=["x"], timeout=180)), \
         patch("db.writer.record_service_health", fake_health), \
         patch("db.writer.ensure_no_replica_for_writers", lambda: None):
        ok = dr._run_scan("cri_scan.py", ["--json"], out, timeout=dr.CRI_SCAN_TIMEOUT_SECS)

    assert ok is False
    assert calls, "timeout must heartbeat cri-scan (else 35m open window pages stale)"
    service, state, kwargs = calls[0]
    assert service == "cri-scan"
    assert state == "error"
    err = kwargs.get("error") or {}
    assert "timed out" in str(err.get("message", "")).lower()
    # This case REQUIRED an embargo. `next_attempt_at` is a writer's own
    # circuit-breaker embargo in this repo, and two consumers read it as
    # documented-normal quiet — `watchdog/check.py` past hysteresis and
    # `incident_watchdog/classify.py` with no hysteresis at all. Rewritten every
    # cycle it is always in the future, so a permanently-failing cri-scan fired
    # once and went silent forever. The half that still matters (the error row
    # exists and names the timeout) is asserted above. R-395.
    assert "next_attempt_at" not in err, (
        f"an ordinary next-cadence retry is not a circuit breaker: {err}"
    )


def test_run_scan_success_does_not_parent_heartbeat(tmp_path, monkeypatch):
    """Child owns the ok heartbeat; parent must not double-write on success."""
    out = tmp_path / "cri.json"
    calls: list[tuple] = []

    def fake_health(service, state, **kwargs):
        calls.append((service, state, kwargs))

    monkeypatch.setattr(dr, "_PROJECT_DIR", tmp_path)
    monkeypatch.setattr(dr, "_SCRIPTS_DIR", tmp_path)
    (tmp_path / "cri_scan.py").write_text("# stub\n")
    result = MagicMock(returncode=0, stdout='{"ok": true}', stderr="")

    with patch.object(dr.subprocess, "run", return_value=result), \
         patch("db.writer.record_service_health", fake_health):
        ok = dr._run_scan("cri_scan.py", ["--json"], out, timeout=dr.CRI_SCAN_TIMEOUT_SECS)

    assert ok is True
    assert calls == []


def test_refresh_passes_raised_cri_budget():
    with patch.object(dr, "_run_scan", return_value=True) as run:
        assert dr._refresh() == 0
        cri_call = next(c for c in run.call_args_list if c.args[0] == "cri_scan.py")
        assert cri_call.kwargs.get("timeout") == dr.CRI_SCAN_TIMEOUT_SECS
        assert cri_call.kwargs["timeout"] >= 180
