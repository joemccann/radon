"""Tests for the cache-write gate in POST /flow-analysis/<ticker>.

Regression: `scripts/fetch_flow.py` swallows every `UWAPIError` subclass
(rate-limit, 5xx, auth) and returns `[]`. The aggregator then produces
a structurally empty "success" payload with `analysis.num_prints == 0`
and `dark_pool.flow_direction == "NO_DATA"`. The GET handler was
serving that as a valid cache for 600s, surfacing as a phantom
"NO DATA" view of healthy tickers. The server now refuses to write the
empty payload to `data/flow_reports/<TICKER>.json` so the next POST
retries against UW and the GET route falls back to the prior valid
cache. Surfaced 2026-05-14 against GOOGL.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _result_with(num_prints: int, daily: list[dict] | None = None) -> SimpleNamespace:
    """Stub the ScriptResult shape that `run_script` returns. Minimum
    fields required by the route: `ok`, `error`, `data`. The `daily`
    arg lets tests express the per-day breakdown that the structural
    gate now inspects.
    """
    return SimpleNamespace(
        ok=True,
        error=None,
        data={
            "ticker": "GOOGL",
            "verdict": {"direction": "NEUTRAL", "confidence": 0},
            "analysis": {"num_prints": num_prints, "score": 0},
            "dark_pool": {
                "flow_direction": "NO_DATA" if num_prints == 0 else "ACCUMULATION",
                "daily": daily or [],
            },
            "options_flow": {},
        },
    )


@pytest.fixture
def app_client(monkeypatch):
    """Late-imported FastAPI TestClient.

    Auth bypass: reach route logic via the trusted-local (server-to-server)
    bypass — the established pattern. Do NOT unset CLERK_JWKS_URL to disable
    auth: the middleware now fails CLOSED on that (see test_auth_fail_closed.py).
    """
    from fastapi.testclient import TestClient
    from api import server  # noqa: WPS433 — import-after-path
    from api import auth

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)

    return TestClient(server.app), server


class TestEmptyAggregateGate:
    def test_skips_cache_write_when_num_prints_zero(self, app_client, tmp_path):
        client, server = app_client
        target = tmp_path / "GOOGL.json"
        cache_writes: list[Path] = []

        async def _fake_run_script(*_args, **_kwargs):
            return _result_with(num_prints=0)

        def _fake_write_cache(path, data):
            cache_writes.append(path)

        with patch.object(server, "run_script", _fake_run_script), \
             patch.object(server, "_write_cache", _fake_write_cache), \
             patch.object(server, "_FLOW_REPORTS_DIR", tmp_path):

            resp = client.post("/flow-analysis/GOOGL")

        assert resp.status_code == 200
        # Response body is still the raw aggregator output so the UI can
        # show the user the result of *this* POST. Only the persistent
        # cache is gated.
        assert resp.json()["analysis"]["num_prints"] == 0
        # The cache was NOT written — the prior valid cache (if any)
        # is preserved.
        assert cache_writes == [], "Expected _write_cache to be skipped on empty aggregate"
        assert not target.exists()

    def test_writes_cache_when_num_prints_positive(self, app_client, tmp_path):
        client, server = app_client
        cache_writes: list[Path] = []

        async def _fake_run_script(*_args, **_kwargs):
            return _result_with(num_prints=2500)

        def _fake_write_cache(path, data):
            cache_writes.append(path)

        with patch.object(server, "run_script", _fake_run_script), \
             patch.object(server, "_write_cache", _fake_write_cache), \
             patch.object(server, "_FLOW_REPORTS_DIR", tmp_path):

            resp = client.post("/flow-analysis/GOOGL")

        assert resp.status_code == 200
        assert resp.json()["analysis"]["num_prints"] == 2500
        assert len(cache_writes) == 1
        assert cache_writes[0].name == "GOOGL.json"

    def test_writes_cache_when_analysis_missing_falsy_path(self, app_client, tmp_path):
        """Safety: a malformed payload with no `analysis` key counts as
        zero. Don't poison the cache with structurally broken data.
        """
        client, server = app_client
        cache_writes: list[Path] = []

        async def _fake_run_script(*_args, **_kwargs):
            return SimpleNamespace(
                ok=True,
                error=None,
                data={"ticker": "GOOGL", "verdict": {"direction": "NEUTRAL"}},
            )

        def _fake_write_cache(path, data):
            cache_writes.append(path)

        with patch.object(server, "run_script", _fake_run_script), \
             patch.object(server, "_write_cache", _fake_write_cache), \
             patch.object(server, "_FLOW_REPORTS_DIR", tmp_path):

            resp = client.post("/flow-analysis/GOOGL")

        assert resp.status_code == 200
        assert cache_writes == []


class TestPerDayBlankGate:
    """Regression for the 2026-05-15 EWY incident: 3 healthy days kept the
    aggregate num_prints > 0 even though the 2 most recent trading days
    silently swallowed a UW rate-limit and showed zero prints. The
    aggregate-only guard waved that through. The per-day guard now
    refuses to write when any trading-day row reports zero prints.
    """

    def test_skips_cache_when_any_trading_day_is_blank(self, app_client, tmp_path):
        client, server = app_client
        cache_writes: list[Path] = []

        # 3 healthy days + 2 zero days. Aggregate is 1499 — passes the
        # old guard but should fail the new per-day check. All five
        # dates are real Mon-Fri trading days in May 2026.
        daily = [
            {"date": "2026-05-15", "num_prints": 0,   "flow_direction": "NO_DATA"},
            {"date": "2026-05-14", "num_prints": 0,   "flow_direction": "NO_DATA"},
            {"date": "2026-05-13", "num_prints": 499, "flow_direction": "DISTRIBUTION"},
            {"date": "2026-05-12", "num_prints": 500, "flow_direction": "ACCUMULATION"},
            {"date": "2026-05-11", "num_prints": 500, "flow_direction": "DISTRIBUTION"},
        ]

        async def _fake_run_script(*_args, **_kwargs):
            return _result_with(num_prints=1499, daily=daily)

        def _fake_write_cache(path, data):
            cache_writes.append(path)

        with patch.object(server, "run_script", _fake_run_script), \
             patch.object(server, "_write_cache", _fake_write_cache), \
             patch.object(server, "_FLOW_REPORTS_DIR", tmp_path):

            resp = client.post("/flow-analysis/GOOGL")

        assert resp.status_code == 200
        assert cache_writes == [], "blank trading day must skip cache write"

    def test_writes_cache_when_all_trading_days_have_data(self, app_client, tmp_path):
        client, server = app_client
        cache_writes: list[Path] = []

        daily = [
            {"date": "2026-05-15", "num_prints": 329, "flow_direction": "DISTRIBUTION"},
            {"date": "2026-05-14", "num_prints": 311, "flow_direction": "NEUTRAL"},
            {"date": "2026-05-13", "num_prints": 499, "flow_direction": "DISTRIBUTION"},
            {"date": "2026-05-12", "num_prints": 500, "flow_direction": "ACCUMULATION"},
            {"date": "2026-05-11", "num_prints": 500, "flow_direction": "DISTRIBUTION"},
        ]

        async def _fake_run_script(*_args, **_kwargs):
            return _result_with(num_prints=2139, daily=daily)

        def _fake_write_cache(path, data):
            cache_writes.append(path)

        with patch.object(server, "run_script", _fake_run_script), \
             patch.object(server, "_write_cache", _fake_write_cache), \
             patch.object(server, "_FLOW_REPORTS_DIR", tmp_path):

            resp = client.post("/flow-analysis/GOOGL")

        assert resp.status_code == 200
        assert len(cache_writes) == 1

    def test_zero_prints_on_a_weekend_does_not_block_cache(self, app_client, tmp_path):
        """Saturday / Sunday rows with zero prints are legit and must not
        gate the cache write. The trading-calendar check is what makes
        the per-day guard targeted rather than blanket-strict.
        """
        client, server = app_client
        cache_writes: list[Path] = []

        # 2026-05-09 is a Saturday — UW won't have darkpool data; should
        # not block the cache write.
        daily = [
            {"date": "2026-05-15", "num_prints": 329, "flow_direction": "DISTRIBUTION"},
            {"date": "2026-05-14", "num_prints": 311, "flow_direction": "NEUTRAL"},
            {"date": "2026-05-13", "num_prints": 499, "flow_direction": "DISTRIBUTION"},
            {"date": "2026-05-12", "num_prints": 500, "flow_direction": "ACCUMULATION"},
            {"date": "2026-05-09", "num_prints": 0,   "flow_direction": "NO_DATA"},
        ]

        async def _fake_run_script(*_args, **_kwargs):
            return _result_with(num_prints=1639, daily=daily)

        def _fake_write_cache(path, data):
            cache_writes.append(path)

        with patch.object(server, "run_script", _fake_run_script), \
             patch.object(server, "_write_cache", _fake_write_cache), \
             patch.object(server, "_FLOW_REPORTS_DIR", tmp_path):

            resp = client.post("/flow-analysis/GOOGL")

        assert resp.status_code == 200
        assert len(cache_writes) == 1
