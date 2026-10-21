"""Research admission boundaries: no arbitrary provider paths or demo data."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from api.routes import uw_research as routes

@pytest.fixture
def client(monkeypatch):
    for key in ("NEXT_PUBLIC_RADON_DEMO", "RADON_DEMO_MODE", "RADON_API_TEST_MODE"):
        monkeypatch.delenv(key, raising=False)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)

def test_bad_ticker_never_reads_store(client, monkeypatch):
    monkeypatch.setattr(routes, "research_snapshot", lambda s: pytest.fail("invalid ticker read store"))
    assert client.get("/uw/research?ticker=../secret").status_code == 400

@pytest.mark.parametrize("endpoint", ["../credentials", "https://other.test/", "stock/AAPL/info?token=x", "companies/AAPL/transcripts/2026Q1", "stock/AAPL/ohlc/1d"])
def test_proxy_is_closed_path_allowlist(client, endpoint):
    assert client.post("/uw/request", json={"endpoint": endpoint}).status_code == 400

def test_proxy_denies_arbitrary_queries(client):
    assert client.post("/uw/request",json={"endpoint":"stock/AAPL/info","params":{"token":"secret"}}).status_code == 400

def test_snapshot_filters_other_tickers_and_preserves_market_context():
    original = {"latest":{"off_lit_trades":{"a":{"payload":{"ticker":"AAPL","price":"1"}},"b":{"payload":{"ticker":"MSFT","price":"2"}}},"market_tide":{"market":{"payload":{"net_call_premium":"100"}}},"periscope:SPX":{"p":{"payload":{"ticker":"SPX","rows":[]}}}}}
    result = routes._ticker_payload(original,"AAPL")
    assert set(result["latest"]["off_lit_trades"]) == {"a"}
    assert "market_tide" in result["latest"] and "periscope:SPX" in result["latest"]
    assert "b" in original["latest"]["off_lit_trades"]

@pytest.mark.parametrize("flag", ["NEXT_PUBLIC_RADON_DEMO", "RADON_API_TEST_MODE"])
def test_demo_deployment_cannot_read_or_refresh(client, monkeypatch, flag):
    monkeypatch.setenv(flag,"1")
    assert client.get("/uw/research?ticker=AAPL").status_code == 403
    assert client.post("/uw/research/refresh",json={"ticker":"AAPL"}).status_code == 403

def test_observations_read_without_provider_call(client, monkeypatch):
    monkeypatch.setattr(routes,"research_snapshot",lambda s:{"ticker":s,"stream":{"status":"missing"}})
    assert client.get("/uw/research?ticker=aapl").json() == {"ticker":"AAPL","stream":{"status":"missing"}}

@pytest.mark.parametrize("params", [{"limit":"1000000"},{"page":"999"},{"limit":"-1"},{"page":"x"}])
def test_proxy_limits_paging_before_spending(client, params):
    assert client.post("/uw/request",json={"endpoint":"news/headlines","params":params}).status_code == 400

def test_proxy_delegates_provider_io_to_killable_admitted_worker(client, monkeypatch):
    from api import subprocess
    worker = AsyncMock(return_value=SimpleNamespace(ok=True, data={"status":200,"data":{"data":[]}}))
    monkeypatch.setattr(subprocess,"run_script",worker)
    response=client.post("/uw/request",json={"endpoint":"news/headlines","params":{"limit":"10"}})
    assert response.status_code == 200
    assert response.json() == {"status":200,"data":{"data":[]}}
    assert worker.await_args.args[0] == "uw_admitted_request.py"
    assert worker.await_args.kwargs["timeout"] == 18
    assert __import__("json").loads(worker.await_args.args[1][1]) == {"limit":"10"}

def test_proxy_sanitizes_killable_worker_failure(client, monkeypatch):
    from api import subprocess
    monkeypatch.setattr(subprocess,"run_script",AsyncMock(return_value=SimpleNamespace(ok=False,error="private provider detail")))
    response=client.post("/uw/request",json={"endpoint":"stock/AAPL/info"})
    assert response.status_code == 503
    assert "private provider detail" not in response.text
