"""REL-036 / R-070: honest exhaustion for budget-blocked / coverage-failed scans.

A budget-blocked universe scan used to exit 0 with a zero-candidate payload:
save_cache refused to persist, mirror_scan_snapshot never ran, NO
service_health row was written, and the FastAPI route stamped the 1h cooldown
for a scan that never ran. Nothing distinguished "quota-blocked, self-clears
at 20:00 ET" from "scanner broken". Both scanners now stamp the payload with
a transient ``scan_status`` marker and write one distinguishable
service_health error row; the routes skip the cooldown stamp on a marked
payload.
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import strength_confirmation_scanner as strength  # noqa: E402
import theta_harvester_scanner as theta  # noqa: E402
from clients.uw_client import UWRateLimitError  # noqa: E402


@pytest.fixture(autouse=True)
def _no_live_scan_ib(monkeypatch) -> None:
    @contextmanager
    def _none():
        yield None

    monkeypatch.setattr(theta, "scan_ib_session", _none)
    monkeypatch.setattr(strength, "scan_ib_session", _none)


PRIOR_GOOD = {
    "scan_time": "2026-08-14T12:00:00+00:00",
    "universe": "preset:ndx100",
    "tickers_scanned": 102,
    "candidates_found": 1,
    "results": [{"ticker": "AAPL"}],
}


# ── utils.scan_health ─────────────────────────────────────────────


def test_next_quota_reset_is_next_2000_et() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import utils.scan_health as scan_health

    et = ZoneInfo("America/New_York")
    before = datetime(2026, 8, 14, 12, 0, tzinfo=et)
    after = datetime(2026, 8, 14, 20, 30, tzinfo=et)
    assert scan_health.next_quota_reset_iso(before) == "2026-08-15T00:00:00Z"
    assert scan_health.next_quota_reset_iso(after) == "2026-08-16T00:00:00Z"


def test_record_scan_degraded_writes_distinguishable_error_row(monkeypatch) -> None:
    import utils.scan_health as scan_health
    from db import writer

    rows: list[tuple] = []
    monkeypatch.setattr(writer, "ensure_no_replica_for_writers", lambda: None)
    monkeypatch.setattr(
        writer, "record_service_health", lambda *a, **k: rows.append((a, k))
    )

    scan_health.record_scan_degraded(
        "theta-harvester",
        scan_health.SCAN_STATUS_BUDGET_BLOCKED,
        "UW daily budget block; universe scan skipped (preset:ndx100)",
        next_attempt_at="2026-08-15T00:00:00Z",
    )

    assert len(rows) == 1
    (service, state), kwargs = rows[0]
    assert service == "theta-harvester"
    assert state == "error"
    assert kwargs["error"]["reason"] == "uw-budget-blocked"
    assert kwargs["error"]["next_attempt_at"] == "2026-08-15T00:00:00Z"
    assert "budget block" in kwargs["error"]["message"]


def test_record_scan_degraded_never_raises(monkeypatch) -> None:
    import utils.scan_health as scan_health
    from db import writer

    def _boom(*_a, **_k):
        raise RuntimeError("no db in this environment")

    monkeypatch.setattr(writer, "ensure_no_replica_for_writers", lambda: None)
    monkeypatch.setattr(writer, "record_service_health", _boom)
    scan_health.record_scan_degraded(
        "theta-harvester", scan_health.SCAN_STATUS_COVERAGE_FAILED, "boom"
    )


# ── theta scanner ─────────────────────────────────────────────────


def _capture_degraded(monkeypatch, module) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(
        module, "record_scan_degraded", lambda *a, **k: calls.append((a, k))
    )
    return calls


def test_theta_budget_block_writes_error_row_and_marks_payload(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "theta_harvester.json"
    monkeypatch.setattr(theta, "_CACHE_PATH", cache)
    monkeypatch.setattr(theta, "should_block_universe_scan", lambda: True)
    monkeypatch.setattr(
        theta, "resolve_tickers", lambda *_a, **_k: (["AAPL", "MSFT"], "preset:ndx100")
    )
    calls = _capture_degraded(monkeypatch, theta)

    payload = theta.scan_universe([])

    assert payload["scan_status"] == "uw-budget-blocked"
    assert len(calls) == 1
    (service, reason, message), kwargs = calls[0]
    assert service == "theta-harvester"
    assert reason == "uw-budget-blocked"
    assert "budget block" in message
    assert kwargs["next_attempt_at"]


def test_theta_budget_block_marker_is_transient_never_persisted(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "theta_harvester.json"
    cache.write_text(json.dumps(PRIOR_GOOD))
    monkeypatch.setattr(theta, "_CACHE_PATH", cache)
    monkeypatch.setattr(theta, "should_block_universe_scan", lambda: True)
    monkeypatch.setattr(
        theta, "resolve_tickers", lambda *_a, **_k: (["AAPL", "MSFT"], "preset:ndx100")
    )
    monkeypatch.setattr(theta, "mirror_scan_snapshot", lambda *_a, **_k: None)
    _capture_degraded(monkeypatch, theta)

    payload = theta.scan_universe([])

    assert payload["scan_status"] == "uw-budget-blocked"
    assert payload["candidates_found"] == 1
    assert theta.save_cache(payload, cache) is True
    stored = json.loads(cache.read_text())
    assert "scan_status" not in stored
    assert stored["candidates_found"] == 1


def test_theta_universe_coverage_failure_writes_error_row(monkeypatch) -> None:
    monkeypatch.setattr(theta, "should_block_universe_scan", lambda: False)
    monkeypatch.setattr(
        theta, "resolve_tickers", lambda *_a, **_k: (["AAPL", "MSFT"], "preset:ndx100")
    )
    monkeypatch.setattr(
        theta,
        "scan_ticker",
        lambda *_a, **_k: (_ for _ in ()).throw(UWRateLimitError("429 slow down")),
    )
    calls = _capture_degraded(monkeypatch, theta)

    payload = theta.scan_universe([], max_workers=2)

    assert payload["scan_status"] == "uw-coverage-failed"
    assert payload["coverage"]["completed"] == 0
    assert len(calls) == 1
    (service, reason, message), _kwargs = calls[0]
    assert service == "theta-harvester"
    assert reason == "uw-coverage-failed"
    assert "0/2" in message


def test_theta_explicit_ticker_rate_limit_writes_no_degraded_row(monkeypatch) -> None:
    monkeypatch.setattr(theta, "should_block_universe_scan", lambda: False)
    monkeypatch.setattr(
        theta,
        "scan_ticker",
        lambda *_a, **_k: (_ for _ in ()).throw(UWRateLimitError("429 slow down")),
    )
    calls = _capture_degraded(monkeypatch, theta)

    payload = theta.scan_universe(["AAPL"])

    assert "scan_status" not in payload
    assert calls == []


# ── strength scanner ──────────────────────────────────────────────


def test_strength_budget_block_writes_error_row_and_marks_payload(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "strength_confirmation.json"
    monkeypatch.setattr(strength, "_CACHE_PATH", cache)
    monkeypatch.setattr(strength, "should_block_universe_scan", lambda: True)
    monkeypatch.setattr(
        strength, "resolve_tickers", lambda *_a, **_k: (["AAPL", "MSFT"], "preset:ndx100")
    )

    def _boom(**_k):
        raise AssertionError("UWClient must not be constructed when budget is blocked")

    monkeypatch.setattr(strength, "UWClient", _boom)
    calls = _capture_degraded(monkeypatch, strength)

    payload = strength.scan_universe([])

    assert payload["scan_status"] == "uw-budget-blocked"
    assert len(calls) == 1
    (service, reason, _message), kwargs = calls[0]
    assert service == "strength-confirmation"
    assert reason == "uw-budget-blocked"
    assert kwargs["next_attempt_at"]


def test_strength_budget_block_marker_never_persisted(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "strength_confirmation.json"
    cache.write_text(json.dumps(PRIOR_GOOD))
    monkeypatch.setattr(strength, "_CACHE_PATH", cache)
    monkeypatch.setattr(strength, "should_block_universe_scan", lambda: True)
    monkeypatch.setattr(
        strength, "resolve_tickers", lambda *_a, **_k: (["AAPL", "MSFT"], "preset:ndx100")
    )
    monkeypatch.setattr(strength, "mirror_scan_snapshot", lambda *_a, **_k: None)
    _capture_degraded(monkeypatch, strength)

    payload = strength.scan_universe([])

    assert payload["scan_status"] == "uw-budget-blocked"
    assert strength.save_cache(payload, cache) is True
    stored = json.loads(cache.read_text())
    assert "scan_status" not in stored


# ── FastAPI routes: degraded scans must not consume the cooldown ──


@pytest.fixture
def app_client(monkeypatch):
    """Late-imported FastAPI TestClient via the trusted-local auth bypass."""
    from fastapi.testclient import TestClient
    from api import server  # noqa: WPS433 — import-after-path
    from api import auth

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)

    return TestClient(server.app), server


def _script_result(data: dict) -> SimpleNamespace:
    return SimpleNamespace(ok=True, error=None, data=data)


THETA_BLOCKED_PAYLOAD = {
    "scan_status": "uw-budget-blocked",
    "universe": "preset:ndx100",
    "params": {"min_dte": 7, "max_dte": 45, "min_credit": 0.0},
    "candidates_found": 0,
    "results": [],
}

# Far enough in the past that the cooldown pre-check always passes even on a
# freshly booted CI host where time.monotonic() is small.
COOLDOWN_EXPIRED = -100_000.0


def test_theta_route_blocked_scan_does_not_consume_cooldown(app_client, monkeypatch) -> None:
    client, server = app_client

    async def _fake_run_script(*_a, **_k):
        return _script_result(dict(THETA_BLOCKED_PAYLOAD))

    monkeypatch.setattr(server, "run_script", _fake_run_script)
    monkeypatch.setattr(server, "_theta_last_scan", COOLDOWN_EXPIRED)

    resp = client.post("/theta-harvester/scan")

    assert resp.status_code == 200
    assert resp.json()["scan_status"] == "uw-budget-blocked"
    assert server._theta_last_scan == COOLDOWN_EXPIRED


def test_theta_route_clean_scan_still_consumes_cooldown(app_client, monkeypatch) -> None:
    client, server = app_client

    async def _fake_run_script(*_a, **_k):
        return _script_result({"universe": "preset:ndx100", "candidates_found": 0, "results": []})

    monkeypatch.setattr(server, "run_script", _fake_run_script)
    monkeypatch.setattr(server, "_theta_last_scan", COOLDOWN_EXPIRED)

    resp = client.post("/theta-harvester/scan")

    assert resp.status_code == 200
    assert server._theta_last_scan > COOLDOWN_EXPIRED


def test_strength_route_blocked_scan_does_not_consume_cooldown(app_client, monkeypatch) -> None:
    client, server = app_client

    async def _fake_run_script(*_a, **_k):
        return _script_result(
            {
                "scan_status": "uw-budget-blocked",
                "universe": "preset:ndx100",
                "candidates_found": 0,
                "results": [],
            }
        )

    monkeypatch.setattr(server, "run_script", _fake_run_script)
    monkeypatch.setattr(server, "_strength_last_scan", COOLDOWN_EXPIRED)

    resp = client.post("/strength-confirmation/scan")

    assert resp.status_code == 200
    assert resp.json()["scan_status"] == "uw-budget-blocked"
    assert server._strength_last_scan == COOLDOWN_EXPIRED
