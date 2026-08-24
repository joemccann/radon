"""GET /uw/usage — operator UW daily quota snapshot from uw_budget."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def budget_path(tmp_path, monkeypatch):
    from utils import uw_budget

    path = tmp_path / "uw_budget.json"
    monkeypatch.setattr(uw_budget, "BUDGET_PATH", path)
    return path


@pytest.fixture
def trusted_client(monkeypatch, budget_path):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    return TestClient(server.app)


@pytest.fixture
def untrusted_client(monkeypatch, budget_path):
    from scripts.api import auth, server

    monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/.well-known/jwks.json")
    monkeypatch.delenv("RADON_AUTH_DISABLED", raising=False)
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: False)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: False)
    monkeypatch.setattr(server, "verify_api_key", lambda request: None)

    async def _deny_jwt(request):
        raise HTTPException(status_code=401, detail="Invalid token")

    monkeypatch.setattr(server, "verify_clerk_jwt", _deny_jwt)
    monkeypatch.setattr(auth, "verify_clerk_jwt", _deny_jwt)
    return TestClient(server.app)


@pytest.fixture
def clerk_client(monkeypatch, budget_path):
    from scripts.api import auth, server

    monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/.well-known/jwks.json")
    monkeypatch.delenv("RADON_AUTH_DISABLED", raising=False)
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: False)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: False)
    monkeypatch.setattr(server, "verify_api_key", lambda request: None)

    async def _ok_jwt(request):
        return {"sub": "user_operator"}

    monkeypatch.setattr(server, "verify_clerk_jwt", _ok_jwt)
    monkeypatch.setattr(auth, "verify_clerk_jwt", _ok_jwt)
    return TestClient(server.app)


def _seed(path: Path, count: int) -> None:
    from utils.uw_budget import quota_date

    path.write_text(json.dumps({"date": quota_date(), "count": count}))


def _expected(used: int, blocked: bool, callers=(), endpoints=()) -> dict:
    from utils.uw_budget import DAILY_LIMIT, quota_date

    return {
        # R-172: a present-but-unreadable state file reads as SPENT, so the
        # payload has to say which of the two it is.
        "state_unreadable": False,
        "used": used,
        "limit": 40000,
        "remaining": DAILY_LIMIT - used,
        "reset_et": "20:00",
        "quota_day": quota_date(),
        "universe_scans_blocked": blocked,
        # Attribution: a bare total cannot say who spent the quota.
        "top_callers": list(callers),
        "top_endpoints": list(endpoints),
    }


def test_uw_usage_not_auth_exempt():
    from scripts.api.server import AUTH_EXEMPT_PATHS

    assert "/uw/usage" not in AUTH_EXEMPT_PATHS


def test_uw_usage_route_registered():
    from scripts.api.server import app

    paths = {
        (route.path, verb)
        for route in app.routes
        for verb in getattr(route, "methods", set()) or set()
    }
    assert ("/uw/usage", "GET") in paths


def test_uw_usage_trusted_local_reads_budget(trusted_client, budget_path):
    _seed(budget_path, 1234)
    response = trusted_client.get("/uw/usage")
    assert response.status_code == 200
    assert response.json() == _expected(1234, False)


def test_uw_usage_missing_file_is_unused(trusted_client, budget_path):
    assert not budget_path.exists()
    response = trusted_client.get("/uw/usage")
    assert response.status_code == 200
    assert response.json() == _expected(0, False)


def test_uw_usage_universe_blocked_at_half_cap(trusted_client, budget_path):
    from utils.uw_budget import UNIVERSE_BLOCK_AT

    _seed(budget_path, UNIVERSE_BLOCK_AT)
    response = trusted_client.get("/uw/usage")
    assert response.status_code == 200
    assert response.json() == _expected(UNIVERSE_BLOCK_AT, True)


def test_uw_usage_untrusted_denied(untrusted_client):
    response = untrusted_client.get("/uw/usage")
    assert response.status_code in {401, 403}


def test_uw_usage_clerk_jwt_allowed(clerk_client, budget_path):
    _seed(budget_path, 7)
    response = clerk_client.get("/uw/usage")
    assert response.status_code == 200
    assert response.json() == _expected(7, False)


def test_uw_usage_surfaces_the_top_spenders(trusted_client, budget_path):
    """The operator question at 85% used is who spent it, not how much is left."""
    from utils.uw_budget import record_hits

    record_hits(9, path=budget_path, caller="garch_convergence", endpoint="stock/AAPL/ohlc/1d")
    record_hits(2, path=budget_path, caller="web", endpoint="stock/NVDA/info")

    payload = trusted_client.get("/uw/usage").json()
    assert payload["used"] == 11
    assert payload["top_callers"] == [
        {"name": "garch_convergence", "hits": 9},
        {"name": "web", "hits": 2},
    ]
    assert payload["top_endpoints"] == [
        {"name": "stock/<T>/ohlc/1d", "hits": 9},
        {"name": "stock/<T>/info", "hits": 2},
    ]
