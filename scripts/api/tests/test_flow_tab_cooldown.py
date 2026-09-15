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
