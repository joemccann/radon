"""PERF-034: GET /earnings must not spawn on a fresh per-ticker cache hit.

Earnings dates do not move intra-session. Theta-harvester GET backfill and
assistant /earnings/{ticker} still spawned earnings_dates.py (new interpreter,
UW session, one GET per ticker) even when data/earnings_dates/{TICKER}.json
was written minutes earlier. A stamped cache inside the 6h TTL must return
the file and spawn 0 times; a stale cache still goes live.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from scripts.api import server


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "_EARNINGS_DATES_DIR", tmp_path)
    return TestClient(server.app, raise_server_exceptions=False)


def _payload(ticker: str = "AAPL", *, days_until: int = 12) -> dict:
    report = (datetime.now(timezone.utc).date() + timedelta(days=days_until)).isoformat()
    return {
        "ticker": ticker,
        "report_date": report,
        "report_time": "postmarket",
        "days_until": days_until,
        "source": "company",
        "expected_move_pct": 4.2,
        "within_dte": True,
        "dte": 16,
        "missing": False,
    }


def _write_cache(tmp_path, ticker: str, payload: dict, *, age_s: float = 60.0) -> None:
    path = tmp_path / f"{ticker}.json"
    path.write_text(json.dumps(payload))
    stamped = datetime.now(timezone.utc).timestamp() - age_s
    os.utime(path, (stamped, stamped))


def test_fresh_earnings_cache_spawns_zero_subprocesses(client, monkeypatch, tmp_path):
    _write_cache(tmp_path, "AAPL", _payload())
    runner = AsyncMock()
    monkeypatch.setattr(server, "run_script", runner)

    response = client.get("/earnings/aapl?dte=16")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ticker"] == "AAPL"
    assert body["missing"] is False
    assert body["report_time"] == "postmarket"
    assert runner.await_count == 0, (
        f"subprocess spawns {runner.await_count} "
        "(want 0 on a fresh earnings-dates cache hit)"
    )


def test_fresh_earnings_batch_spawns_zero_when_every_ticker_is_cached(
    client, monkeypatch, tmp_path
):
    _write_cache(tmp_path, "AAPL", _payload("AAPL"))
    _write_cache(tmp_path, "MSFT", _payload("MSFT", days_until=20))
    runner = AsyncMock()
    monkeypatch.setattr(server, "run_script", runner)

    response = client.get("/earnings?tickers=AAPL,MSFT&dte=16")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 2
    assert [row["ticker"] for row in body["results"]] == ["AAPL", "MSFT"]
    assert runner.await_count == 0, (
        f"subprocess spawns {runner.await_count} "
        "(want 0 when every batch ticker has a fresh cache)"
    )


def test_stale_earnings_cache_still_goes_live(client, monkeypatch, tmp_path):
    _write_cache(tmp_path, "AAPL", _payload(), age_s=7 * 60 * 60)
    live = _payload(days_until=11)
    runner = AsyncMock(
        return_value=server.ScriptResult(ok=True, data=live, error=None, exit_code=0)
    )
    monkeypatch.setattr(server, "run_script", runner)

    response = client.get("/earnings/aapl")

    assert response.status_code == 200, response.text
    assert response.json()["days_until"] == 11
    runner.assert_awaited_once()


def test_failed_refresh_recomputes_days_until_for_this_session(
    client, monkeypatch, tmp_path
):
    """A live miss must not keep days_until frozen for an older session.

    The panel derives earnings risk from days_until. A file written when the
    print was 40 days out, read after the live script fails, was still
    reporting 40. REL-335 / R-746.
    """
    from datetime import date

    today = date(2026, 10, 30)
    monkeypatch.setattr(server, "_earnings_today_et", lambda: today)
    stale = _payload("MSFT", days_until=40)
    stale["report_date"] = "2026-11-01"
    stale["within_dte"] = False
    stale["dte"] = 45
    _write_cache(tmp_path, "MSFT", stale, age_s=7 * 60 * 60)
    _write_cache(tmp_path, "AAPL", _payload("AAPL"))
    runner = AsyncMock(
        return_value=server.ScriptResult(
            ok=False, data=None, error="UW down", exit_code=1
        )
    )
    monkeypatch.setattr(server, "run_script", runner)

    single = client.get("/earnings/MSFT?dte=1")
    assert single.status_code == 200, single.text
    body = single.json()
    assert body["days_until"] == 2
    assert body["within_dte"] is False
    assert body["dte"] == 1
    assert body["report_date"] == "2026-11-01"

    batch = client.get("/earnings?tickers=AAPL,MSFT&dte=1")
    assert batch.status_code == 200, batch.text
    rows = {row["ticker"]: row for row in batch.json()["results"]}
    assert rows["MSFT"]["days_until"] == 2
    assert rows["MSFT"]["within_dte"] is False
    assert rows["AAPL"]["missing"] is False
    assert runner.await_count == 2


def test_batch_live_failure_with_no_fresh_hit_stays_502(client, monkeypatch, tmp_path):
    stale = _payload("MSFT", days_until=40)
    _write_cache(tmp_path, "MSFT", stale, age_s=7 * 60 * 60)
    runner = AsyncMock(
        return_value=server.ScriptResult(
            ok=False, data=None, error="UW down", exit_code=1
        )
    )
    monkeypatch.setattr(server, "run_script", runner)

    response = client.get("/earnings?tickers=MSFT&dte=16")

    assert response.status_code == 502
    assert "UW down" in response.json()["detail"]
