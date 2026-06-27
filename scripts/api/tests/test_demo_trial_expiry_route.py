"""Tests for POST /demo/trial-expiry (demo.radon.run, Phase 2).

The endpoint is a thin auth-exempt wrapper over utils.demo_trial.
trial_expiry_handler (the calendar math is covered by test_demo_trial.py); here
we pin the HTTP contract: correct payload shape, 400 on bad input, and that it
is reachable WITHOUT auth (it is in AUTH_EXEMPT_PATHS, so the demo signup
webhook can call it server-to-server with no user JWT).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Must be set before importing server (test_mode is read at import time).
os.environ.setdefault("RADON_API_TEST_MODE", "1")

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture()
def client():
    from scripts.api import server

    return TestClient(server.app)


def test_trial_expiry_returns_window(client):
    # A Wednesday signup → expires 16:00 ET Friday (Wed=1, Thu=2, Fri=3).
    resp = client.post(
        "/demo/trial-expiry",
        json={"start_iso_et": "2026-06-24T09:30:00-04:00"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["trading_days"] == 3
    assert body["expires_at"].startswith("2026-06-26T16:00:00")
    assert "started_at" in body and "active" in body


def test_trial_expiry_custom_trading_days(client):
    resp = client.post(
        "/demo/trial-expiry",
        json={"start_iso_et": "2026-06-24T09:30:00-04:00", "trading_days": 1},
    )
    assert resp.status_code == 200, resp.text
    # 1 trading day from a Wednesday → expires that Wednesday.
    assert resp.json()["expires_at"].startswith("2026-06-24T16:00:00")


def test_trial_expiry_requires_start(client):
    resp = client.post("/demo/trial-expiry", json={})
    assert resp.status_code == 400


def test_trial_expiry_is_auth_exempt(client):
    # No auth headers, no CLERK_JWKS_URL configured in the test env — an
    # exempt path must still return 200 (it short-circuits before the gate).
    resp = client.post(
        "/demo/trial-expiry",
        json={"start_iso_et": "2026-06-24T09:30:00-04:00"},
    )
    assert resp.status_code == 200
