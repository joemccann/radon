"""HTTP contracts for workstation overlays and analytics form submissions.

All subprocess calls and overlay cache reads are isolated from live sources.
"""
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from scripts.api import server


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "_INFORMED_FLOW_DIR", tmp_path / "informed")
    monkeypatch.setattr(server, "_EVENT_ODDS_DIR", tmp_path / "odds")
    return TestClient(server.app, raise_server_exceptions=False)


@pytest.mark.parametrize("route,script,empty_field", [
    ("informed-flow", "fetch_informed_flow.py", "congress_trades"),
    ("event-odds", "fetch_event_odds.py", "overlays"),
])
@pytest.mark.parametrize("state", ["live", "cache", "empty", "failed"])
def test_overlay_state_contract(client, monkeypatch, route, script, empty_field, state):
    payload = {"ticker": "AAPL", empty_field: [{"source": "fixture"}]}
    result = server.ScriptResult(
        ok=state in ("live", "empty"),
        data=payload if state == "live" else None,
        error="upstream unavailable" if state in ("cache", "failed") else None,
        exit_code=0 if state in ("live", "empty") else 1,
    )
    runner = AsyncMock(return_value=result)
    monkeypatch.setattr(server, "run_script", runner)
    monkeypatch.setattr(server, "_read_cache", lambda path: payload if state == "cache" else None)
    response = client.get(f"/{route}/aapl")
    runner.assert_awaited_once_with(script, ["AAPL", "--json"], timeout=60)
    if state == "failed":
        assert response.status_code == 502
        assert response.json()["detail"] == "upstream unavailable"
    else:
        assert response.status_code == 200
        if state == "empty":
            assert response.json()["missing"] is True
            assert response.json()[empty_field] == []
        else:
            assert response.json() == payload


@pytest.mark.parametrize("route", ["informed-flow", "event-odds"])
def test_invalid_overlay_ticker_never_starts_subprocess(client, monkeypatch, route):
    runner = AsyncMock()
    monkeypatch.setattr(server, "run_script", runner)
    assert client.get(f"/{route}/INVALID!").status_code == 400
    runner.assert_not_awaited()


@pytest.mark.parametrize("route,field", [
    ("forecast/chronos", "horizon"), ("forecast/chronos", "lookback"),
    ("flow-surprise", "top"), ("flow-surprise", "lookback"),
])
@pytest.mark.parametrize("value", ["invalid", None, [], 0, -1, True, 1.5])
def test_invalid_analytics_integer_returns_client_error(client, monkeypatch, route, field, value):
    runner = AsyncMock()
    monkeypatch.setattr(server, "run_script", runner)
    response = client.post(f"/{route}", json={"ticker": "AAPL", field: value})
    assert response.status_code == 400
    assert response.json()["detail"] == f"{field} must be a positive integer"
    runner.assert_not_awaited()


@pytest.mark.parametrize("route,field", [
    ("forecast/chronos", "horizon"), ("forecast/chronos", "lookback"),
    ("flow-surprise", "top"), ("flow-surprise", "lookback"),
])
def test_oversized_analytics_integer_string_returns_client_error(client, monkeypatch, route, field):
    runner = AsyncMock()
    monkeypatch.setattr(server, "run_script", runner)
    response = client.post(f"/{route}", json={"ticker": "AAPL", field: "9" * 4301})
    assert response.status_code == 400
    assert response.json()["detail"] == f"{field} must be a positive integer"
    runner.assert_not_awaited()


@pytest.mark.parametrize("route", ["forecast/chronos", "flow-surprise"])
@pytest.mark.parametrize("body", [[], None, "invalid"])
def test_analytics_requires_object_body(client, monkeypatch, route, body):
    runner = AsyncMock()
    monkeypatch.setattr(server, "run_script", runner)
    response = client.post(f"/{route}", content=json.dumps(body), headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    runner.assert_not_awaited()


@pytest.mark.parametrize("route", ["forecast/chronos", "flow-surprise"])
def test_analytics_malformed_json_returns_client_error(client, monkeypatch, route):
    runner = AsyncMock()
    monkeypatch.setattr(server, "run_script", runner)
    response = client.post(f"/{route}", content="{", headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Body must be a JSON object"
    runner.assert_not_awaited()


@pytest.mark.parametrize("route,script,args,timeout", [
    ("forecast/chronos", "chronos_forecast.py",
     ["--ticker", "AAPL", "--metric", "flow_strength", "--horizon", "10", "--lookback", "120", "--json"], 180),
    ("flow-surprise", "flow_surprise.py",
     ["--metric", "flow_strength", "--top", "20", "--lookback", "250", "--ticker", "AAPL"], 240),
])
@pytest.mark.parametrize("state", ["defaults", "numeric_strings", "upstream_failure", "demo"])
def test_analytics_submission_contract(client, monkeypatch, route, script, args, timeout, state):
    runner = AsyncMock(return_value=server.ScriptResult(
        ok=state != "upstream_failure", data={"results": []},
        error="source unavailable" if state == "upstream_failure" else None,
        exit_code=1 if state == "upstream_failure" else 0,
    ))
    monkeypatch.setattr(server, "run_script", runner)
    body = {"ticker": "aapl"}
    if state == "numeric_strings":
        body.update({"horizon": "10", "top": "20", "lookback": "120" if route == "forecast/chronos" else "250"})
    if state == "demo":
        monkeypatch.setattr(server, "test_mode", True)
    response = client.post(f"/{route}", json=body)
    if state == "demo":
        assert response.status_code == 200
        runner.assert_not_awaited()
    else:
        runner.assert_awaited_once_with(script, args, timeout=timeout)
        if state == "upstream_failure":
            assert response.status_code == 502
            assert response.json()["detail"] == "source unavailable"
        else:
            assert response.status_code == 200
            assert response.json() == {"results": []}
