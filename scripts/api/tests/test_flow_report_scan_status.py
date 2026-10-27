"""A flow scan always lands, and its outcome is recorded where the UI can read it.

2026-10-27: /flow-analysis/QQQ served a 2026-06-23 cache for four months. Every
scan was killed at the 225s deadline walking 20 uncached 40-page days, and the
only trace was a journald line: the page kept saying "scan running" until its
5-minute poll gave up.

1. The scan passes a history budget that fits the shortest run the capacity
   chain will seat, so the script returns a (possibly partial) report instead
   of being killed. Skipped days backfill from the per-day cache next scan.
2. ``<TICKER>.scan.json`` records running / ok / refused / failed beside the
   report, so the Next.js GET can end the client's poll with the real reason.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(API_DIR.parent))

import server  # noqa: E402

GOOD = {
    "ticker": "QQQ",
    "analysis": {"num_prints": 3000},
    "dark_pool": {"daily": [{"date": "2026-10-27", "num_prints": 3000}]},
}


@pytest.fixture
def reports(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_FLOW_REPORTS_DIR", tmp_path)
    return tmp_path


def _fake_runner(result, seen):
    async def _run(script, args, **kwargs):
        seen.append((script, list(args), kwargs))
        return result

    return _run


def _status(reports: Path) -> dict:
    return json.loads((reports / "QQQ.scan.json").read_text())


def test_the_scan_passes_a_history_budget_that_fits_the_shortest_seated_run(reports, monkeypatch):
    seen: list = []
    monkeypatch.setattr(
        server, "_run_script_retrying_capacity",
        _fake_runner(server.ScriptResult(ok=True, data=GOOD, error=None), seen),
    )
    asyncio.run(server._scan_and_cache("QQQ"))
    script, args, _ = seen[0]
    assert script == "flow_report.py"
    i = args.index("--history-budget-secs")
    budget = float(args[i + 1])
    # Today's walk, options alerts, skew and analysis still run after the
    # budget; the run must end inside the shortest attempt the chain seats.
    assert 0 < budget <= server.FLOW_REPORT_MIN_RUN_SECS - 30


def test_success_writes_the_report_and_an_ok_status(reports, monkeypatch):
    monkeypatch.setattr(
        server, "_run_script_retrying_capacity",
        _fake_runner(server.ScriptResult(ok=True, data=GOOD, error=None), []),
    )
    asyncio.run(server._scan_and_cache("QQQ"))
    assert json.loads((reports / "QQQ.json").read_text())["ticker"] == "QQQ"
    status = _status(reports)
    assert status["state"] == "ok"
    assert status["started_at"] <= status["finished_at"]
    assert status.get("error") is None


def test_a_failed_scan_records_the_reason_and_still_raises(reports, monkeypatch):
    monkeypatch.setattr(
        server, "_run_script_retrying_capacity",
        _fake_runner(server.ScriptResult(ok=False, data=None, error="Script flow_report.py timed out after 225s"), []),
    )
    with pytest.raises(HTTPException):
        asyncio.run(server._scan_and_cache("QQQ"))
    status = _status(reports)
    assert status["state"] == "failed"
    assert "timed out" in status["error"]
    assert not (reports / "QQQ.json").exists()


def test_a_report_refused_by_the_cache_gate_is_recorded(reports, monkeypatch):
    empty = {"ticker": "QQQ", "analysis": {"num_prints": 0}, "dark_pool": {"daily": []}}
    monkeypatch.setattr(
        server, "_run_script_retrying_capacity",
        _fake_runner(server.ScriptResult(ok=True, data=empty, error=None), []),
    )
    asyncio.run(server._scan_and_cache("QQQ"))
    status = _status(reports)
    assert status["state"] == "refused"
    assert status["error"]


def test_the_status_is_running_while_the_script_runs(reports, monkeypatch):
    observed: list = []

    async def _run(script, args, **kwargs):
        observed.append(_status(reports)["state"])
        return server.ScriptResult(ok=True, data=GOOD, error=None)

    monkeypatch.setattr(server, "_run_script_retrying_capacity", _run)
    asyncio.run(server._scan_and_cache("QQQ"))
    assert observed == ["running"]


def test_status_errors_are_scrubbed_and_bounded(reports, monkeypatch):
    long_error = "token=sk-live-SECRETSECRET " + "x" * 2000
    monkeypatch.setattr(
        server, "_run_script_retrying_capacity",
        _fake_runner(server.ScriptResult(ok=False, data=None, error=long_error), []),
    )
    with pytest.raises(HTTPException):
        asyncio.run(server._scan_and_cache("QQQ"))
    error = _status(reports)["error"]
    assert len(error) <= 500
