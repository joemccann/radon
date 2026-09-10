"""REL-184 (R-515): the dispersion Yahoo rung stops on a throttle."""
from __future__ import annotations

import sys
import time
import urllib.error
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fetch_dispersion as fd  # noqa: E402


def _throttled(monkeypatch):
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise urllib.error.HTTPError("u", 429, "too many", {}, None)

    monkeypatch.setattr(fd, "_yahoo_get", boom)
    return calls


class TestYahoo429CircuitBreak:
    def test_backfill_abandons_after_consecutive_429s(self, monkeypatch, capsys):
        calls = _throttled(monkeypatch)
        symbols = [f"S{i}" for i in range(500)]
        fetched = fd._fetch_yahoo_backfill(symbols, time.monotonic() + 300)
        assert fetched == {}
        assert calls["n"] <= 3, (
            f"a 429 storm was deepened with {calls['n']} chart calls"
        )
        assert "429" in capsys.readouterr().err

    def test_incremental_abandons_after_consecutive_429s(self, monkeypatch, capsys):
        calls = _throttled(monkeypatch)
        symbols = [f"S{i}" for i in range(500)]
        fetched = fd._fetch_yahoo_incremental(
            symbols, time.monotonic() + 300, "1mo"
        )
        assert fetched == {}
        assert calls["n"] <= 3

    def test_non_throttle_failures_do_not_trip_the_breaker(self, monkeypatch):
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            raise urllib.error.URLError("conn reset")

        monkeypatch.setattr(fd, "_yahoo_get", flaky)
        symbols = [f"S{i}" for i in range(10)]
        fd._fetch_yahoo_backfill(symbols, time.monotonic() + 300)
        assert calls["n"] == 10
