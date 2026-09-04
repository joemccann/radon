"""Route tests for normalized options exposure data."""
from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from clients.menthorq_dashboard_client import (  # noqa: E402
    MenthorQDashboardAuthEmbargoed,
    MenthorQDashboardAuthError,
    MenthorQDashboardPayloadError,
    MenthorQDashboardTimeoutError,
    MenthorQDashboardUpstreamError,
)


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)


@pytest.fixture
def client():
    from scripts.api.server import app

    return TestClient(app)


def _payload() -> dict:
    return {
        "schema_version": 1,
        "symbol": "MU",
        "source": "menthorq_dashboard",
        "source_time": "2026-07-16T20:00:00",
        "fetched_at": "2026-07-17T03:00:00Z",
        "frequency": "eod",
        "spot": 853.2,
        "strikes": [850.0],
        "expirations": [{"expiration_date": "2026-07-17", "dte": 1}],
        "cells": {
            "strike_idx": [0],
            "expiration_idx": [0],
            "net_gex": [-8_480_000.0],
            "abs_gex": [8_480_000.0],
            "net_dex": [-125_000.0],
            "abs_dex": [125_000.0],
            "oi_call": [100],
            "oi_put": [420],
        },
        "levels": [],
        "units": {},
        "complete": True,
    }


def test_route_returns_normalized_payload_and_forwards_frequency(client):
    provider = type("Provider", (), {"fetch_exposure": lambda self, symbol, frequency: _payload()})()

    with patch("scripts.api.server.MenthorQDashboardClient", return_value=provider) as factory:
        response = client.get("/options/exposure/MU?frequency=eod")

    assert response.status_code == 200
    assert response.json() == _payload()
    factory.assert_called_once_with()


@pytest.mark.parametrize(
    "path",
    [
        "/options/exposure/mu?frequency=eod",
        "/options/exposure/MU%24?frequency=eod",
        "/options/exposure/TOO-LONG-SYMBOL?frequency=eod",
        "/options/exposure/MU?frequency=realtime",
    ],
)
def test_route_rejects_invalid_symbol_or_frequency_with_400(client, path):
    response = client.get(path)

    assert response.status_code == 400
    assert response.json()["detail"] in {"Invalid symbol", "Invalid frequency"}


@pytest.mark.parametrize(
    ("provider_error", "expected_status", "expected_detail"),
    [
        (
            MenthorQDashboardAuthError("secret token here"),
            503,
            "Options exposure authentication is unavailable",
        ),
        (
            MenthorQDashboardTimeoutError("upstream timeout with secrets"),
            504,
            "Options exposure provider timed out",
        ),
        (
            MenthorQDashboardPayloadError("private response payload"),
            503,
            "Options exposure data is unavailable",
        ),
        (
            MenthorQDashboardUpstreamError("private response payload"),
            503,
            "Options exposure data is unavailable",
        ),
    ],
)
def test_route_maps_provider_failures_to_sanitized_statuses(
    client, provider_error, expected_status, expected_detail
):
    def _raise(*_args):
        raise provider_error

    provider = type("Provider", (), {"fetch_exposure": _raise})()

    with patch("scripts.api.server.MenthorQDashboardClient", return_value=provider):
        response = client.get("/options/exposure/MU?frequency=eod")

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert "secret" not in response.text.lower()
    assert "private" not in response.text.lower()


def test_route_maps_auth_embargo_to_transient_detail(client):
    def _raise(*_args):
        raise MenthorQDashboardAuthEmbargoed(
            "dashboard authentication embargoed after a recent failure"
        )

    provider = type("Provider", (), {"fetch_exposure": _raise})()

    with patch("scripts.api.server.MenthorQDashboardClient", return_value=provider):
        response = client.get("/options/exposure/SPX?frequency=eod")

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "embargo" in detail.lower()

    from monitor_daemon.handlers import menthorq_login_probe

    assert menthorq_login_probe._is_auth_failure(503, detail) is False
