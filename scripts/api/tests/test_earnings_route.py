"""Tests for GET /earnings/{ticker} and GET /earnings?tickers= batch.

Mirrors informed-flow / ticker-ratings: patch ``server.run_script`` so the
route logic is exercised without a live UW subprocess.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    from scripts.api import server, auth

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    yield


@pytest.fixture(autouse=True)
def force_live_mode(monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "test_mode", False)
    yield


@pytest.fixture
def client():
    from scripts.api.server import app

    return TestClient(app)


def _fake_script_result(ok=True, data=None, error=None, exit_code=0):
    from api.subprocess import ScriptResult

    return ScriptResult(ok=ok, data=data, error=error, exit_code=exit_code)


def _hona_payload(*, dte=None, within_dte=None, missing=False):
    return {
        "ticker": "HONA",
        "report_date": "2026-08-05",
        "report_time": "postmarket",
        "days_until": 0,
        "source": "company",
        "expected_move_pct": 8.76,
        "within_dte": within_dte,
        "dte": dte,
        "missing": missing,
    }


# ── GET /earnings/{ticker} ───────────────────────────────────────────


def test_earnings_single_happy_path(client):
    payload = _hona_payload(dte=16, within_dte=True)
    fake = _fake_script_result(ok=True, data=payload)

    async def _stub(*_args, **_kwargs):
        return fake

    with patch("scripts.api.server.run_script", side_effect=_stub) as run_mock:
        response = client.get("/earnings/hona?dte=16")

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "HONA"
    assert body["missing"] is False
    assert body["within_dte"] is True
    assert body["report_time"] == "postmarket"
    args = run_mock.call_args.args
    assert args[0] == "earnings_dates.py"
    assert args[1] == ["HONA", "--dte", "16", "--json"]


def test_earnings_single_without_dte(client):
    payload = _hona_payload(dte=None, within_dte=None)
    fake = _fake_script_result(ok=True, data=payload)

    async def _stub(*_args, **_kwargs):
        return fake

    with patch("scripts.api.server.run_script", side_effect=_stub) as run_mock:
        response = client.get("/earnings/HONA")

    assert response.status_code == 200
    assert response.json()["within_dte"] is None
    assert run_mock.call_args.args[1] == ["HONA", "--json"]


def test_earnings_single_missing_is_200(client):
    """Legitimate unknown/empty earnings → 200 + missing, never 4xx."""
    missing = {
        "ticker": "ZZZZZ",
        "report_date": None,
        "report_time": None,
        "days_until": None,
        "source": None,
        "expected_move_pct": None,
        "within_dte": None,
        "dte": 16,
        "missing": True,
    }
    fake = _fake_script_result(ok=True, data=missing)

    async def _stub(*_args, **_kwargs):
        return fake

    with patch("scripts.api.server.run_script", side_effect=_stub):
        response = client.get("/earnings/ZZZZZ?dte=16")

    assert response.status_code == 200
    assert response.json()["missing"] is True
    assert response.json()["ticker"] == "ZZZZZ"


def test_earnings_single_invalid_ticker_400(client):
    async def _boom(*_args, **_kwargs):
        raise AssertionError("invalid ticker must fail before subprocess")

    with patch("scripts.api.server.run_script", side_effect=_boom):
        response = client.get("/earnings/MU1")

    assert response.status_code == 400
    assert "Invalid ticker" in response.json()["detail"]


def test_earnings_single_cache_fallback_on_script_failure(client, monkeypatch):
    from scripts.api import server

    cached = _hona_payload(dte=16, within_dte=True)
    fake = _fake_script_result(ok=False, error="UW token expired", exit_code=1)

    async def _stub(*_args, **_kwargs):
        return fake

    monkeypatch.setattr(
        server,
        "_read_cache",
        lambda path: cached if path.name == "HONA.json" else None,
    )

    with patch("scripts.api.server.run_script", side_effect=_stub):
        response = client.get("/earnings/HONA?dte=16")

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "HONA"
    assert body["missing"] is False
    assert body["report_date"] == "2026-08-05"


def test_earnings_single_502_when_no_cache(client, monkeypatch):
    from scripts.api import server

    fake = _fake_script_result(ok=False, error="script crashed", exit_code=1)

    async def _stub(*_args, **_kwargs):
        return fake

    monkeypatch.setattr(server, "_read_cache", lambda _path: None)

    with patch("scripts.api.server.run_script", side_effect=_stub):
        response = client.get("/earnings/HONA")

    assert response.status_code == 502
    assert "script crashed" in response.json()["detail"]


# ── GET /earnings?tickers= ───────────────────────────────────────────


def test_earnings_batch_happy_path(client):
    rows = [
        _hona_payload(dte=16, within_dte=True),
        {
            **_hona_payload(dte=16, within_dte=False),
            "ticker": "AAPL",
            "days_until": 40,
            "within_dte": False,
        },
    ]
    fake = _fake_script_result(ok=True, data=rows)

    async def _stub(*_args, **_kwargs):
        return fake

    with patch("scripts.api.server.run_script", side_effect=_stub) as run_mock:
        response = client.get("/earnings?tickers=HONA,AAPL&dte=16")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert body["dte"] == 16
    assert [r["ticker"] for r in body["results"]] == ["HONA", "AAPL"]
    args = run_mock.call_args.args
    assert args[0] == "earnings_dates.py"
    assert args[1] == ["HONA", "AAPL", "--dte", "16", "--json"]


def test_earnings_batch_dedupes_and_uppercases(client):
    fake = _fake_script_result(ok=True, data=[_hona_payload()])

    async def _stub(*_args, **_kwargs):
        return fake

    with patch("scripts.api.server.run_script", side_effect=_stub) as run_mock:
        response = client.get("/earnings?tickers=hona,HONA,hona")

    assert response.status_code == 200
    assert run_mock.call_args.args[1] == ["HONA", "--json"]


def test_earnings_batch_rejects_invalid_ticker(client):
    async def _boom(*_args, **_kwargs):
        raise AssertionError("invalid ticker must fail before subprocess")

    with patch("scripts.api.server.run_script", side_effect=_boom):
        response = client.get("/earnings?tickers=AAPL,BAD!")

    assert response.status_code == 400
    assert "Invalid ticker" in response.json()["detail"]


def test_earnings_batch_rejects_over_cap(client):
    too_many = ",".join(f"T{i:04d}"[:5] for i in range(51))
    # T0000 etc may not match _TICKER_RE (digits). Use letter symbols.
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    tickers = []
    for i in range(51):
        # 1-5 letter synthetic symbols
        a = letters[i % 26]
        b = letters[(i // 26) % 26]
        c = letters[(i // 676) % 26]
        tickers.append(f"{a}{b}{c}")
    raw = ",".join(tickers)

    async def _boom(*_args, **_kwargs):
        raise AssertionError("over-cap must fail before subprocess")

    with patch("scripts.api.server.run_script", side_effect=_boom):
        response = client.get(f"/earnings?tickers={raw}")

    assert response.status_code == 400
    assert "max 50" in response.json()["detail"]


def test_earnings_batch_empty_tickers_400(client):
    async def _boom(*_args, **_kwargs):
        raise AssertionError("empty tickers must fail before subprocess")

    with patch("scripts.api.server.run_script", side_effect=_boom):
        response = client.get("/earnings?tickers=")

    assert response.status_code == 400
