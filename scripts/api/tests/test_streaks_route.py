"""GET /streaks/{ticker} — source ladder, cache short-circuit, missing contract.

The fetchers are unit-stubbed at the module seam (`api.routes.streaks.*`);
the IB pool machinery itself is covered by test_historical_pool.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    yield


@pytest.fixture
def client():
    from scripts.api.server import app

    return TestClient(app)


def _closes(n: int, start_day: int = 1) -> dict[str, float]:
    """n ascending closes across July/August 2026 (strictly rising)."""
    out: dict[str, float] = {}
    for i in range(n):
        day = start_day + i
        month = 7 + (day - 1) // 28
        out[f"2026-{month:02d}-{((day - 1) % 28) + 1:02d}"] = 100.0 + i
    return out


async def _no_ib(request, symbol):  # pragma: no cover - trivial stub
    return {}


def test_uw_serves_when_ib_unavailable(client):
    with patch("api.routes.streaks._read_cached_closes", return_value=None), patch(
        "api.routes.streaks._fetch_ib_closes", new=_no_ib
    ), patch(
        "api.routes.streaks._fetch_uw_closes", return_value=_closes(30)
    ), patch(
        "api.routes.streaks._write_cached_closes"
    ) as write_cache:
        resp = client.get("/streaks/spy")

    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "SPY"
    assert body["source"] == "uw"
    assert body["missing"] is False
    assert body["count"] == 30
    # 30 strictly rising closes: streak climbs to 29 on the last session.
    assert body["current"]["streak"] == 29
    assert body["stats"]["max_streak"] == 29
    assert body["stats"]["runs_total"] == 1
    assert write_cache.call_count == 1


def test_ib_wins_when_available(client):
    async def _ib(request, symbol):
        return _closes(25)

    uw = MagicMock()
    with patch("api.routes.streaks._read_cached_closes", return_value=None), patch(
        "api.routes.streaks._fetch_ib_closes", new=_ib
    ), patch("api.routes.streaks._fetch_uw_closes", uw), patch(
        "api.routes.streaks._write_cached_closes"
    ):
        resp = client.get("/streaks/SPY")

    assert resp.status_code == 200
    assert resp.json()["source"] == "ib"
    uw.assert_not_called()


def test_short_sources_fall_through_to_yahoo(client):
    with patch("api.routes.streaks._read_cached_closes", return_value=None), patch(
        "api.routes.streaks._fetch_ib_closes", new=_no_ib
    ), patch(
        "api.routes.streaks._fetch_uw_closes", return_value=_closes(3)
    ), patch(
        "api.routes.streaks._fetch_rh_closes", return_value={}
    ), patch(
        "api.routes.streaks._fetch_yahoo_closes", return_value=_closes(30)
    ), patch(
        "api.routes.streaks._write_cached_closes"
    ):
        resp = client.get("/streaks/SPY")

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "yahoo"
    assert body["count"] == 30


def test_robinhood_outranks_yahoo_when_it_serves_enough_bars(client):
    from api.routes.streaks import MIN_ACCEPT_BARS, RH_SOURCE

    yahoo = MagicMock()
    with patch("api.routes.streaks._read_cached_closes", return_value=None), patch(
        "api.routes.streaks._fetch_ib_closes", new=_no_ib
    ), patch(
        "api.routes.streaks._fetch_uw_closes", return_value={}
    ), patch(
        "api.routes.streaks._fetch_rh_closes", return_value=_closes(MIN_ACCEPT_BARS)
    ), patch(
        "api.routes.streaks._fetch_yahoo_closes", yahoo
    ), patch(
        "api.routes.streaks._write_cached_closes"
    ):
        resp = client.get("/streaks/SPY")

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == RH_SOURCE  # REL-174 (R-485): the writer vocabulary spelling
    assert body["count"] == MIN_ACCEPT_BARS
    yahoo.assert_not_called()


def test_longest_short_result_used_when_every_source_is_short(client):
    with patch("api.routes.streaks._read_cached_closes", return_value=None), patch(
        "api.routes.streaks._fetch_ib_closes", new=_no_ib
    ), patch(
        "api.routes.streaks._fetch_uw_closes", return_value=_closes(3)
    ), patch(
        "api.routes.streaks._fetch_rh_closes", return_value=_closes(5)
    ), patch(
        "api.routes.streaks._fetch_yahoo_closes", return_value=_closes(4)
    ), patch(
        "api.routes.streaks._write_cached_closes"
    ):
        resp = client.get("/streaks/SPY")

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "rh"  # REL-174 (R-485): the writer vocabulary spelling
    assert body["count"] == 5


def test_cache_hit_short_circuits_the_ladder(client):
    uw = MagicMock()
    with patch(
        "api.routes.streaks._read_cached_closes", return_value=_closes(25)
    ), patch("api.routes.streaks._fetch_uw_closes", uw):
        resp = client.get("/streaks/SPY")

    assert resp.status_code == 200
    assert resp.json()["source"] == "cache"
    uw.assert_not_called()


def test_all_sources_empty_is_missing_200(client):
    with patch("api.routes.streaks._read_cached_closes", return_value=None), patch(
        "api.routes.streaks._fetch_ib_closes", new=_no_ib
    ), patch(
        "api.routes.streaks._fetch_uw_closes", return_value={}
    ), patch(
        "api.routes.streaks._fetch_rh_closes", return_value={}
    ), patch(
        "api.routes.streaks._fetch_yahoo_closes", return_value={}
    ), patch(
        "api.routes.streaks._write_cached_closes"
    ) as write_cache:
        resp = client.get("/streaks/XYZ")

    assert resp.status_code == 200
    body = resp.json()
    assert body["missing"] is True
    assert body["symbol"] == "XYZ"
    assert body["series"] == []
    # Empty-payload guard: never cache an empty result.
    write_cache.assert_not_called()


def test_invalid_ticker_is_400(client):
    resp = client.get("/streaks/ba%24d")
    assert resp.status_code == 400


def test_robinhood_rung_label_is_the_writer_vocabulary():
    """REL-174 (R-485): one spelling for the source everywhere."""
    from api.routes import streaks
    from clients.robinhood_client import RH_SOURCE

    assert streaks.RH_SOURCE == RH_SOURCE == "rh"
    assert [label for label, _ in streaks.FALLBACK_LADDER] == ["uw", "rh", "yahoo"]
    assert all(callable(getattr(streaks, name)) for _, name in streaks.FALLBACK_LADDER)
