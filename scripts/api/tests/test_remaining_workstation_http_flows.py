"""Bounded HTTP journeys for the remaining read/backtest transport boundaries."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request


@pytest.fixture
def bounded(monkeypatch):
    from scripts.api import auth, server
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "ib_pool", None)
    monkeypatch.setattr(server, "uw_available", False)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 3, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(server, "datetime", Clock)
    # Do not enter TestClient's context: that would run the live service lifespan.
    return server, TestClient(server.app)


def test_backtest_registry_http(bounded, monkeypatch):
    server, client = bounded
    import backtest.strategies as strategies
    registry = [{"name": "vcg", "wired": True}]
    monkeypatch.setattr(strategies, "list_strategies", lambda: registry)
    response = client.get("/backtest")
    assert response.status_code == 200
    assert response.json() == {"strategies": registry}


@pytest.mark.parametrize("ok,status", [(True, 200), (False, 502)])
def test_backtest_cache_miss_and_subprocess_error_http(bounded, monkeypatch, ok, status):
    server, client = bounded
    calls = []
    monkeypatch.setattr(server, "_load_latest_backtest_run", lambda strategy: None)

    async def run(script, args, timeout):
        calls.append((script, args, timeout))
        return SimpleNamespace(ok=ok, data={"strategy": "vcg", "fresh": True}, error="bounded runner failed")

    monkeypatch.setattr(server, "run_script", run)
    response = client.get("/backtest/vcg")
    assert response.status_code == status
    assert response.json() == ({"strategy": "vcg", "fresh": True} if ok else {"detail": "bounded runner failed"})
    assert calls == [("backtest_run.py", ["--strategy", "vcg", "--persist"], 180)]


def test_backtest_disconnect_cancels_runner_http(bounded, monkeypatch):
    server, client = bounded
    events = []
    monkeypatch.setattr(server, "_load_latest_backtest_run", lambda strategy: None)

    async def run(*args, **kwargs):
        events.append("started")
        try:
            await asyncio.Event().wait()
        finally:
            events.append("cancelled")

    async def disconnected(self):
        return True

    monkeypatch.setattr(server, "run_script", run)
    monkeypatch.setattr(Request, "is_disconnected", disconnected)
    response = client.get("/backtest/vcg")
    assert response.status_code == 499
    assert response.json() == {"detail": "client disconnected"}
    assert events == ["started", "cancelled"]


@pytest.mark.parametrize("probe,fresh,uw_error,source,shortable", [
    ({"difficulty": 3.0, "shortable_shares": 500}, True, False, "ib", True),
    ({"difficulty": None, "shortable_shares": None}, True, False, "uw", None),
    ({"difficulty": None, "shortable_shares": None}, False, False, "none", None),
    (RuntimeError("probe failed"), True, False, "uw", None),
    (asyncio.TimeoutError(), True, False, "uw", None),
    (RuntimeError("probe failed"), True, True, "none", None),
])
def test_short_availability_priority_freshness_and_errors_http(bounded, monkeypatch, probe, fresh, uw_error, source, shortable):
    server, client = bounded
    calls = []

    @asynccontextmanager
    async def acquire(role):
        assert role == "data"
        yield "bounded-client"

    monkeypatch.setattr(server, "ib_pool", SimpleNamespace(is_connected=lambda role: role == "data", acquire=acquire))
    monkeypatch.setattr(server, "uw_available", True)

    def probe_ib(client, ticker):
        calls.append(("ib", client, ticker))
        if isinstance(probe, Exception):
            raise probe
        return probe

    def fetch_uw(ticker):
        calls.append(("uw", ticker))
        if uw_error:
            raise RuntimeError("UW unavailable")
        return {"ticker": ticker, "date": "2026-10-03"}

    monkeypatch.setattr(server, "_probe_short_ticks_in_thread", probe_ib)
    monkeypatch.setattr(server, "_fetch_uw_short_data", fetch_uw)
    monkeypatch.setattr(server, "_uw_short_data_is_fresh", lambda raw, ticker: fresh)
    monkeypatch.setattr(server, "_extract_uw_fee_rebate", lambda raw: (1.25, -0.1, "2026-10-03T11:59:00Z"))
    response = client.get("/short-availability/aapl")
    assert response.status_code == 200
    ib_data = probe if isinstance(probe, dict) else {}
    accepted_uw = fresh and not uw_error
    assert response.json() == {
        "ticker": "AAPL", "shortable": shortable,
        "difficulty": ib_data.get("difficulty"), "shortable_shares": ib_data.get("shortable_shares"),
        "fee_rate": 1.25 if accepted_uw else None, "rebate_rate": -0.1 if accepted_uw else None,
        "source": source, "missing": source == "none",
        "as_of": "2026-10-03T11:59:00Z" if source == "uw" else "2026-10-03T12:00:00+00:00",
    }
    assert calls == [("ib", "bounded-client", "AAPL"), ("uw", "AAPL")]


@pytest.mark.parametrize("ticker", ["AAPL", "invalid!"])
def test_short_availability_absent_or_invalid_is_measurement_empty_http(bounded, ticker):
    server, client = bounded
    response = client.get(f"/short-availability/{ticker}")
    assert response.status_code == 200
    assert response.json() == {
        "ticker": ticker.upper(), "shortable": None, "difficulty": None, "shortable_shares": None,
        "fee_rate": None, "rebate_rate": None, "source": "none", "missing": True,
        "as_of": "2026-10-03T12:00:00+00:00",
    }


def test_cash_flows_db_query_type_filter_summary_and_sync_http(bounded, monkeypatch):
    server, client = bounded
    calls = []
    rows = [(1, "2026-10-02", "Deposit", 100, "USD", "funding", "D", "2026-10-02T12:00:00Z"),
            (2, "2026-10-03", "Withdrawal", -25, "USD", "cash out", "W", "2026-10-03T09:00:00Z"),
            (3, "2026-10-03", "Dividend", 4, "USD", "dividend", "DIV", "2026-10-03T10:00:00Z")]

    def execute(sql, params):
        calls.append((" ".join(sql.split()), params))
        return rows

    sync = {"state": "error", "last_error": {"message": "Flex throttled"}, "next_attempt_at": "2026-10-03T13:00:00Z"}
    monkeypatch.setattr(server.db_http, "hrana_execute", execute)
    monkeypatch.setattr(server, "_load_cash_flow_sync_status", lambda: sync)
    response = client.get("/cash-flows?days=2&types=Deposit,%20Withdrawal")
    assert response.status_code == 200
    names = ["id", "date", "type", "amount", "currency", "description", "raw_type", "synced_at"]
    assert response.json() == {"rows": [dict(zip(names, row)) for row in rows[:2]], "count": 2,
        "from_date": "2026-10-01", "summary": {"deposits": 100, "withdrawals": -25, "dividends": 0, "net": 75},
        "last_synced_at": "2026-10-03T09:00:00Z", "sync_status": sync, "db_error": None}
    assert calls == [("SELECT id, date, type, amount, currency, description, raw_type, synced_at FROM cash_flows WHERE date >= ? ORDER BY date DESC, id DESC", ("2026-10-01",))]


@pytest.mark.parametrize("fallback_error", [False, True])
def test_cash_flows_json_fallback_cutoff_and_missing_http(bounded, monkeypatch, tmp_path, fallback_error):
    server, client = bounded
    from utils import atomic_io
    calls = []
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server.db_http, "hrana_execute", lambda *args: (_ for _ in ()).throw(RuntimeError("DB offline")))
    monkeypatch.setattr(server, "_load_cash_flow_sync_status", lambda: {"state": "unknown"})
    row = {"id": 1, "date": "2026-10-02", "type": "Dividend", "amount": 4, "currency": "USD", "description": "paid", "raw_type": "DIV", "synced_at": None}

    def load(path):
        calls.append(path)
        if fallback_error:
            raise OSError("snapshot absent")
        return {"rows": [row, {**row, "date": "2026-10-01", "amount": 99}]}

    monkeypatch.setattr(atomic_io, "verified_load", load)
    response = client.get("/cash-flows?days=0")
    assert response.status_code == 200
    assert response.json() == {"rows": [] if fallback_error else [row], "count": 0 if fallback_error else 1,
        "from_date": "2026-10-02", "summary": {"deposits": 0, "withdrawals": 0, "dividends": 0 if fallback_error else 4, "net": 0 if fallback_error else 4},
        "last_synced_at": None, "sync_status": {"state": "unknown"}, "db_error": "DB offline"}
    assert calls == [str(tmp_path / "cash_flows.json")]


def test_cash_flows_all_types_include_interest_and_fees_in_net_http(bounded, monkeypatch):
    server, client = bounded
    rows = [(1, "2026-10-03", "Interest", 3, "USD", "interest", "INT", None),
            (2, "2026-10-03", "Fee", -1, "USD", "fee", "FEE", None)]
    monkeypatch.setattr(server.db_http, "hrana_execute", lambda sql, params: rows)
    monkeypatch.setattr(server, "_load_cash_flow_sync_status", lambda: {"state": "ok"})
    response = client.get("/cash-flows?types=")
    assert response.status_code == 200
    assert [row["type"] for row in response.json()["rows"]] == ["Interest", "Fee"]
    assert response.json()["summary"] == {"deposits": 0, "withdrawals": 0, "dividends": 0, "net": 2}
    assert response.json()["count"] == 2
    assert response.json()["db_error"] is None


def test_cash_flows_json_fallback_retains_type_filter_http(bounded, monkeypatch):
    server, client = bounded
    from utils import atomic_io
    monkeypatch.setattr(server.db_http, "hrana_execute", lambda *args: (_ for _ in ()).throw(RuntimeError("DB offline")))
    monkeypatch.setattr(server, "_load_cash_flow_sync_status", lambda: {"state": "unknown"})
    dividend = {"id": 1, "date": "2026-10-03", "type": "Dividend", "amount": 4, "synced_at": "2026-10-03T10:00:00Z"}
    fee = {"id": 2, "date": "2026-10-03", "type": "Fee", "amount": -1, "synced_at": "2026-10-03T11:00:00Z"}
    monkeypatch.setattr(atomic_io, "verified_load", lambda path: {"rows": [dividend, fee]})
    response = client.get("/cash-flows?types=Dividend")
    assert response.status_code == 200
    assert response.json()["rows"] == [dividend]
    assert response.json()["summary"] == {"deposits": 0, "withdrawals": 0, "dividends": 4, "net": 4}
    assert response.json()["last_synced_at"] == "2026-10-03T10:00:00Z"
    assert response.json()["db_error"] == "DB offline"
