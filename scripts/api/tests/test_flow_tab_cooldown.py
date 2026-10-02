"""Hourly cooldown for scanner / discover / flow-analysis FastAPI POSTs."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = SCRIPTS_DIR.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    from scripts.api import server, auth

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    yield


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "_flow_tab_last", {})
    monkeypatch.setattr(server, "FLOW_TAB_COOLDOWN_S", 3600)
    return tmp_path


@pytest.fixture
def client():
    from scripts.api.server import app

    return TestClient(app)


def _fake(ok=True, data=None, error=None):
    from api.subprocess import ScriptResult

    return ScriptResult(ok=ok, data=data or {"scan_time": "t", "results": [1]}, error=error)


async def _stub_ok(*args, **kwargs):
    return _fake()


def test_discover_uses_min_alerts_3_and_two_dp_pages(client, monkeypatch):
    with patch("scripts.api.server.run_script", side_effect=_stub_ok) as run_mock:
        res = client.post("/discover")
    assert res.status_code == 200
    assert run_mock.call_args.args[0] == "discover.py"
    assert run_mock.call_args.args[1] == ["--min-alerts", "3", "--dp-pages", "2"]


def test_flow_analysis_timeout_matches_wrapper_scan_budget(client):
    """Close-of-day flow-analysis must not die under the wrapper's curl budget.

    2026-09-15 20:00Z (page 8e4a8285): capacity-shed retry then
    `flow-analysis FastAPI outcome indeterminate (curl=0, http=502)` at +128s.
    Same ~120s kill on 2026-09-11 20:00Z. Wrapper SCAN_TIMEOUT default is 180s
    and /discover already uses 180; the tighter API budget pages the oneshot.
    """
    wrapper = (REPO_ROOT / "scripts" / "run_flow_refresh.sh").read_text()
    match = re.search(
        r'SCAN_TIMEOUT="\$\{RADON_FLOW_REFRESH_SCAN_TIMEOUT:-(\d+)\}"',
        wrapper,
    )
    assert match, "wrapper SCAN_TIMEOUT default missing"
    wrapper_budget = int(match.group(1))

    with patch("scripts.api.server.run_script", side_effect=_stub_ok) as run_mock:
        res = client.post("/flow-analysis?force=true")
    assert res.status_code == 200
    assert run_mock.call_args.args[0] == "flow_analysis.py"
    assert run_mock.call_args.kwargs.get("timeout") == wrapper_budget, (
        f"API timeout {run_mock.call_args.kwargs.get('timeout')} must equal "
        f"wrapper SCAN_TIMEOUT default {wrapper_budget}"
    )


def test_second_discover_inside_hour_serves_cache(client, monkeypatch):
    with patch("scripts.api.server.run_script", side_effect=_stub_ok) as run_mock:
        first = client.post("/discover")
        second = client.post("/discover")
    assert first.status_code == 200
    assert second.status_code == 200
    assert run_mock.call_count == 1
    assert second.json() == first.json()


def test_force_bypasses_discover_cooldown(client, monkeypatch):
    with patch("scripts.api.server.run_script", side_effect=_stub_ok) as run_mock:
        client.post("/discover")
        res = client.post("/discover?force=true")
    assert res.status_code == 200
    assert run_mock.call_count == 2


def test_scored_discover_with_one_provider_skip_is_cached_not_http_400(
    client, isolated_data_dir,
):
    """The hourly discover POST must keep a scan that scored candidates.

    Page b224647c0c4bb160c0be3aae3f182dff, 2026-10-02 14:02:26Z: discover.py
    exited 0 after writing darkpool caches, the payload carried
    ``error: required provider data unavailable`` plus candidates, and
    ``_run_flow_tab`` turned that into HTTP 400. The wrapper logged
    ``discover FastAPI outcome indeterminate (curl=0, http=400)`` and the
    oneshot exited 1. A total miss (no candidates) stays 400.
    """
    import json
    from unittest.mock import MagicMock, patch

    from clients.uw_client import UWAPIError
    from discover import discover

    alerts = []
    for ticker in ("AAPL", "MSFT"):
        alerts.extend(
            {
                "ticker": ticker,
                "total_premium": 600_000,
                "type": "CALL",
                "volume_oi_ratio": 2.5,
                "has_sweep": True,
                "sector": "Technology",
                "marketcap": 1_000_000_000,
                "underlying_price": 100,
                "issue_type": "Common Stock",
            }
            for _ in range(3)
        )

    def _fetch(ticker, *args, **kwargs):
        if ticker == "MSFT":
            raise UWAPIError("budget")
        return {
            "aggregate": {
                "buy_ratio": 0.8,
                "direction": "ACCUMULATION",
                "strength": 70.0,
                "prints": 12,
            },
            "daily": [],
            "sustained_days": 2,
            "total_prints": 12,
        }

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    with patch("discover.UWClient", return_value=mock_client), \
         patch("discover.fetch_options_flow", return_value=alerts), \
         patch("discover.get_existing_tickers", return_value=set()), \
         patch("discover.fetch_darkpool_multi", side_effect=_fetch):
        payload = discover(min_alerts=3, dp_pages=2)

    async def _run(*args, **kwargs):
        return _fake(data=payload)

    with patch("scripts.api.server.run_script", side_effect=_run):
        res = client.post("/discover?force=true")

    assert res.status_code == 200, res.text
    written = json.loads((isolated_data_dir / "discover.json").read_text())
    assert written["candidates"][0]["ticker"] == "AAPL"
    assert "error" not in written
    assert written["degraded"] is True


def test_discover_with_no_candidates_and_a_hard_error_stays_http_400(
    client, isolated_data_dir,
):
    async def _run(*args, **kwargs):
        return _fake(data={
            "discovery_time": "2026-10-02T14:02:26+00:00",
            "candidates": [],
            "candidates_found": 0,
            "degraded": True,
            "error": "required provider data unavailable",
        })

    with patch("scripts.api.server.run_script", side_effect=_run):
        res = client.post("/discover?force=true")

    assert res.status_code == 400
    assert not (isolated_data_dir / "discover.json").exists()


def test_scan_cooldown_does_not_call_script_twice(client, monkeypatch):
    with patch("scripts.api.server.run_script", side_effect=_stub_ok) as run_mock:
        client.post("/scan")
        client.post("/scan")
    assert run_mock.call_count == 1


def test_at_cooldown_boundary_plain_post_is_cached_but_force_spends(client, monkeypatch):
    """A scheduled POST landing at 3599.x s must spend only when it passes force."""
    import time

    from scripts.api import server

    with patch("scripts.api.server.run_script", side_effect=_stub_ok) as run_mock:
        client.post("/scan")
        server._flow_tab_last["scan"] = time.monotonic() - (server.FLOW_TAB_COOLDOWN_S - 1)
        plain = client.post("/scan")
        assert plain.status_code == 200
        assert run_mock.call_count == 1
        forced = client.post("/scan?force=true")
        assert forced.status_code == 200
        assert run_mock.call_count == 2
