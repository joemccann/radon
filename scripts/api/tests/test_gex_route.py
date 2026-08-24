"""Ticker identity guards for the shared GEX scan cache."""

import asyncio

from scripts.api import server
from scripts.api.scan_gate import ScanGate
from scripts.api.subprocess import ScriptResult


def test_cooldown_never_returns_another_tickers_cache(monkeypatch):
    monkeypatch.setattr(server, "test_mode", False)
    gate = ScanGate("gex")
    gate.mark_success()
    monkeypatch.setitem(server.SCAN_GATES, "gex", gate)
    monkeypatch.setattr(server, "_read_cache", lambda _path: {"ticker": "SPY"})
    calls = []

    async def fake_run_script(script, args, timeout=None):
        calls.append((script, args, timeout))
        return ScriptResult(ok=True, data={"ticker": "QQQ", "levels": []})

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "_write_cache", lambda *_args: None)

    result = asyncio.run(server.gex_scan("qqq"))

    assert result["ticker"] == "QQQ"
    assert calls == [("gex_scan.py", ["--json", "--ticker", "QQQ"], 120)]
