"""Tests for the FastAPI /ticker/ratings and /pi/exec endpoints.

Both endpoints replace `spawn(python)` calls from Next.js (CLAUDE.md: "No
spawn() from Next.js"). They share a subprocess-mocking strategy: patch
`api.subprocess.run_script` (the JSON-parsing variant for ratings) and
`api.subprocess.run_script_raw` (the raw stdout/stderr variant for /pi/exec)
so we exercise the route logic without spinning up a real Python child.
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
    """TestClient.host defaults to 'testclient' — coerce auth into treating
    us as a localhost peer so the routes are reachable in tests."""
    from scripts.api import server, auth
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    yield


@pytest.fixture
def client():
    from scripts.api.server import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# /ticker/ratings
# ---------------------------------------------------------------------------

def _fake_script_result(ok=True, data=None, error=None, exit_code=0):
    from api.subprocess import ScriptResult
    return ScriptResult(ok=ok, data=data, error=error, exit_code=exit_code)


def test_ticker_ratings_unwraps_array_payload(client):
    """fetch_analyst_ratings.py --json emits a JSON array. The route
    should unwrap the single-ticker case to a dict so the Next.js layer
    can render it directly."""
    fake = _fake_script_result(
        ok=True,
        data=[{"ticker": "AMD", "consensus": "buy", "buy_pct": 86.7}],
    )

    async def _stub(*args, **kwargs):
        return fake

    with patch("scripts.api.server.run_script", side_effect=_stub) as run_mock:
        response = client.get("/ticker/ratings?ticker=AMD")

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "AMD"
    assert body["consensus"] == "buy"
    # The script gets called with uppercased ticker + --json
    args = run_mock.call_args.args
    assert args[0] == "fetch_analyst_ratings.py"
    assert args[1] == ["AMD", "--json"]


def test_ticker_ratings_returns_object_unchanged(client):
    """If the script ever emits a single object instead of a list, pass
    it through untouched."""
    fake = _fake_script_result(ok=True, data={"ticker": "AMD", "consensus": "hold"})

    async def _stub(*args, **kwargs):
        return fake

    with patch("scripts.api.server.run_script", side_effect=_stub):
        response = client.get("/ticker/ratings?ticker=AMD")

    assert response.status_code == 200
    body = response.json()
    assert body["consensus"] == "hold"


def test_ticker_ratings_returns_empty_dict_for_empty_array(client):
    """Empty array → empty dict (200), so the UI can render "no data"
    gracefully instead of treating it as an error."""
    fake = _fake_script_result(ok=True, data=[])

    async def _stub(*args, **kwargs):
        return fake

    with patch("scripts.api.server.run_script", side_effect=_stub):
        response = client.get("/ticker/ratings?ticker=NONE")

    assert response.status_code == 200
    assert response.json() == {}


def test_ticker_ratings_502_on_subprocess_failure(client):
    """Script exit != 0 surfaces as 502 + detail; matches the contract
    every other run_script-backed route follows."""
    fake = _fake_script_result(ok=False, error="UW token expired", exit_code=1)

    async def _stub(*args, **kwargs):
        return fake

    with patch("scripts.api.server.run_script", side_effect=_stub):
        response = client.get("/ticker/ratings?ticker=AMD")

    assert response.status_code == 502
    assert "UW token expired" in response.json()["detail"]


def test_ticker_ratings_400_on_empty_ticker(client):
    """Empty ticker param trips the explicit guard before we spawn."""
    response = client.get("/ticker/ratings?ticker=")
    # FastAPI treats `ticker=` as an empty string, which we reject.
    # If it ever returns 422 for missing-field validation that's fine too.
    assert response.status_code in (400, 422)


# ---------------------------------------------------------------------------
# /scan
# ---------------------------------------------------------------------------

def test_scan_route_passes_explicit_worker_count(client, monkeypatch):
    """The on-demand scanner route should use the faster bounded worker count."""
    monkeypatch.delenv("RADON_SCANNER_WORKERS", raising=False)
    fake = _fake_script_result(
        ok=True,
        data={
            "scan_time": "2026-06-25T16:00:00Z",
            "tickers_scanned": 0,
            "signals_found": 0,
            "top_signals": [],
        },
    )

    async def _stub(*args, **kwargs):
        return fake

    with patch("scripts.api.server.run_script", side_effect=_stub) as run_mock:
        response = client.post("/scan")

    assert response.status_code == 200
    args = run_mock.call_args.args
    kwargs = run_mock.call_args.kwargs
    assert args[0] == "scanner.py"
    assert args[1] == ["--top", "25", "--workers", "24"]
    assert kwargs["timeout"] == 120


# ---------------------------------------------------------------------------
# /pi/exec
# ---------------------------------------------------------------------------

def _fake_raw_result(ok=True, stdout="", stderr="", exit_code=0, timed_out=False):
    from api.subprocess import RawScriptResult
    return RawScriptResult(
        ok=ok, stdout=stdout, stderr=stderr, exit_code=exit_code, timed_out=timed_out
    )


def test_pi_exec_runs_allowlisted_script(client):
    fake = _fake_raw_result(stdout="Scanner: 3 tickers\nAAPL\n", exit_code=0)

    async def _stub(*args, **kwargs):
        return fake

    with patch("scripts.api.server.run_script_raw", side_effect=_stub) as run_mock:
        response = client.post(
            "/pi/exec",
            json={"script": "scanner.py", "args": ["--top", "5"], "timeout": 60},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "Scanner" in body["stdout"]
    assert body["exit_code"] == 0
    # Confirm the subprocess saw our args
    args, kwargs = run_mock.call_args
    assert args[0] == "scanner.py"
    assert args[1] == ["--top", "5"]


def test_pi_exec_rejects_unknown_script(client):
    """Defence-in-depth: even if upstream parsing mis-routes, we refuse
    anything outside the PI allowlist."""
    response = client.post("/pi/exec", json={"script": "evil.py", "args": []})
    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"].lower()


def test_pi_exec_rejects_missing_script(client):
    response = client.post("/pi/exec", json={"args": []})
    assert response.status_code == 400


def test_pi_exec_rejects_non_string_args(client):
    response = client.post(
        "/pi/exec", json={"script": "scanner.py", "args": [1, 2, 3]}
    )
    assert response.status_code == 400


def test_pi_exec_surfaces_subprocess_failure(client):
    fake = _fake_raw_result(ok=False, stderr="Traceback: KeyError", exit_code=1)

    async def _stub(*args, **kwargs):
        return fake

    with patch("scripts.api.server.run_script_raw", side_effect=_stub):
        response = client.post(
            "/pi/exec", json={"script": "discover.py", "args": []}
        )

    # 200 + ok:false is the contract — chat surface needs to render the
    # stderr to the user. Don't hide it behind a 502.
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "KeyError" in body["stderr"]


# ---------------------------------------------------------------------------
# /pi/exec — READ vs MUTATE tiers (least-privilege)
# ---------------------------------------------------------------------------

def test_pi_exec_read_tier_runs_without_privileged_flag(client):
    """READ-tier scripts (pure scans/analysis) run on the default path —
    no privileged flag required."""
    fake = _fake_raw_result(stdout="Discover: 12 candidates\n", exit_code=0)

    async def _stub(*args, **kwargs):
        return fake

    with patch("scripts.api.server.run_script_raw", side_effect=_stub):
        response = client.post(
            "/pi/exec", json={"script": "discover.py", "args": []}
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_pi_exec_mutate_tier_rejected_without_flag(client):
    """MUTATE-tier scripts (live-account / portfolio-state mutators) are
    refused on the default read-only path."""
    response = client.post(
        "/pi/exec", json={"script": "ib_sync.py", "args": ["--sync"]}
    )
    assert response.status_code == 400
    assert "mutat" in response.json()["detail"].lower()


def test_pi_exec_mutate_tier_allowed_with_flag(client):
    """A MUTATE-tier script runs when the caller passes the explicit
    privileged authorization flag."""
    fake = _fake_raw_result(stdout='{"ok": true}\n', exit_code=0)

    async def _stub(*args, **kwargs):
        return fake

    with patch("scripts.api.server.run_script_raw", side_effect=_stub) as run_mock:
        response = client.post(
            "/pi/exec",
            json={"script": "ib_sync.py", "args": ["--sync"], "allow_mutating": True},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    args, _ = run_mock.call_args
    assert args[0] == "ib_sync.py"
    assert args[1] == ["--sync"]
