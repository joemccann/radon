"""data_refresh must not fail the systemd unit on a graceful scan degradation.

A scan that soft-fails (timeout / bad output) keeps the existing JSON and retries
on the next 15-min run — that's transient, self-healing degradation, not a process
crash. Exiting non-zero marks the unit 'failed' and pages the operator for noise
(cri_scan timed out once post-close 2026-06-15, fine on every neighbouring run).
Data freshness is monitored separately by the cri-scan/vcg-scan staleness watchers.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import data_refresh as dr  # noqa: E402


def test_main_returns_0_on_full_success():
    with patch.object(dr, "_is_trading_day", return_value=True), \
         patch.object(dr, "_run_scan", side_effect=[True, True, True]):
        assert dr.main() == 0


def test_main_returns_0_when_a_scan_soft_fails():
    # cri soft-fails (e.g. 120s timeout), vcg + gex ok — the unit must still succeed.
    with patch.object(dr, "_is_trading_day", return_value=True), \
         patch.object(dr, "_run_scan", side_effect=[False, True, True]):
        assert dr.main() == 0


def test_main_returns_0_when_all_scans_soft_fail():
    with patch.object(dr, "_is_trading_day", return_value=True), \
         patch.object(dr, "_run_scan", side_effect=[False, False, False]):
        assert dr.main() == 0


def test_main_refreshes_gex_without_menthorq():
    # GEX must be on the same cadence as cri/vcg (it had no scheduler of its own),
    # invoked with --no-mq so the durable UW snapshot lands inside the 120s budget.
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
