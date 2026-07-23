"""Contract tests for the isolated MenthorQ dashboard API client."""

from __future__ import annotations

import base64
import json
import stat
import time
from pathlib import Path

import pytest
import requests

from clients.menthorq_dashboard_client import (
    MenthorQDashboardAuthError,
    MenthorQDashboardClient,
    MenthorQDashboardPayloadError,
    MenthorQDashboardUpstreamError,
)


def _jwt(*, expires_at: int | None = None) -> str:
    payload = {"exp": expires_at or int(time.time()) + 3600, "sub": "test-user"}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


def _exposure_payload(*, ticker: str = "MU", frequency: str = "eod") -> dict:
    return {
        "ticker": ticker,
        "timestamp": "2026-07-16T20:00:00",
        "frequency": frequency,
        "spot_price": 853.2,
        "strikes": [850, 855],
        "expirations": [
            {"expiration_date": "2026-07-17", "dte": 1},
            {"expiration_date": "2026-07-24", "dte": 8},
        ],
        "cells": {
            "strike_idx": [0, 1],
            "expiration_idx": [0, 1],
            "net_gex": [-8_480_000.0, 2_000_000.0],
            "abs_gex": [8_480_000.0, 2_000_000.0],
            "net_dex": [-125_000.0, 35_000.0],
            "abs_dex": [125_000.0, 35_000.0],
            "oi_call": [100, 230],
            "oi_put": [420, 110],
        },
    }


def _levels_payload() -> dict:
    return {
        "ticker": "MU",
        "timestamp": "2026-07-16T20:00:00",
        "frequency": "eod",
        "hvl": 900,
        "call_resistance": 910,
        "put_support": 815,
        "call_resistance_0dte": 910,
        "put_support_0dte": 815,
        "max_1d": 904.12,
        "min_1d": 802.28,
    }


class _Response:
    def __init__(self, payload: dict, *, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self) -> dict:
        return self._payload


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


class _TimeoutSession:
    def get(self, _url: str, **_kwargs):
        raise requests.Timeout("request included private transport context")


def test_fetch_uses_frequency_specific_url_and_bearer_header_without_caching():
    token = _jwt()
    session = _Session(
        [
            _Response(_exposure_payload(frequency="intraday")),
            _Response(_levels_payload()),
            _Response(_exposure_payload(frequency="intraday")),
            _Response(_levels_payload()),
        ]
    )
    client = MenthorQDashboardClient(access_token=token, http_session=session)

    first = client.fetch_exposure("MU", "intraday")
    second = client.fetch_exposure("MU", "intraday")

    assert first["cells"] == second["cells"]
    assert first["source_time"] == second["source_time"]
    assert first["fetched_at"] != "" and second["fetched_at"] != ""
    assert len(session.calls) == 4
    assert session.calls[0]["url"].endswith(
        "/options/net-gex-by-expiration/MU?frequency=intraday"
    )
    assert session.calls[1]["url"].endswith("/gamma-levels/MU/eod")
    assert all(call["headers"]["Authorization"] == f"Bearer {token}" for call in session.calls)


def test_passes_provider_usd_gex_through_unscaled_and_maps_all_seven_levels():
    session = _Session([_Response(_exposure_payload()), _Response(_levels_payload())])
    client = MenthorQDashboardClient(access_token=_jwt(), http_session=session)

    result = client.fetch_exposure("MU", "eod")

    assert result["schema_version"] == 1
    assert result["symbol"] == "MU"
    assert result["source"] == "menthorq_dashboard"
    assert result["source_time"] == "2026-07-16T20:00:00"
    assert result["frequency"] == "eod"
    assert result["spot"] == 853.2
    assert result["cells"]["net_gex"] == pytest.approx([-8_480_000.0, 2_000_000.0])
    assert result["cells"]["abs_gex"] == pytest.approx([8_480_000.0, 2_000_000.0])
    assert result["units"] == {
        "spot": "usd_per_share",
        "strike": "usd_per_share",
        "net_gex": "usd_per_1pct_move",
        "abs_gex": "usd_per_1pct_move",
        "net_dex": "usd",
        "abs_dex": "usd",
        "oi_call": "contracts",
        "oi_put": "contracts",
    }
    assert [(level["key"], level["label"], level["value"]) for level in result["levels"]] == [
        ("hvl", "HVL", 900.0),
        ("call_resistance", "CR", 910.0),
        ("put_support", "PS", 815.0),
        ("call_resistance_0dte", "CR 0DTE", 910.0),
        ("put_support_0dte", "PS 0DTE", 815.0),
        ("max_1d", "1D Max", 904.12),
        ("min_1d", "1D Min", 802.28),
    ]
    assert result["complete"] is True


def test_canonicalizes_provider_abs_gex_sign_before_returning_to_consumers():
    exposure = _exposure_payload()
    exposure["cells"]["abs_gex"][0] = -8_480_000.0
    session = _Session([_Response(exposure), _Response(_levels_payload())])
    client = MenthorQDashboardClient(access_token=_jwt(), http_session=session)

    result = client.fetch_exposure("MU", "eod")

    assert result["cells"]["abs_gex"][0] == pytest.approx(8_480_000.0)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["cells"]["expiration_idx"].append(0),
        lambda payload: payload["cells"]["strike_idx"].__setitem__(0, 99),
        lambda payload: payload["cells"]["expiration_idx"].__setitem__(0, -1),
        lambda payload: payload["cells"]["net_gex"].__setitem__(0, float("nan")),
    ],
)
def test_rejects_malformed_parallel_arrays_and_indices(mutate):
    exposure = _exposure_payload()
    mutate(exposure)
    session = _Session([_Response(exposure), _Response(_levels_payload())])
    client = MenthorQDashboardClient(access_token=_jwt(), http_session=session)

    with pytest.raises(MenthorQDashboardPayloadError, match="invalid exposure payload"):
        client.fetch_exposure("MU", "eod")


def test_missing_level_is_explicitly_partial_not_fabricated():
    levels = _levels_payload()
    del levels["min_1d"]
    session = _Session([_Response(_exposure_payload()), _Response(levels)])
    client = MenthorQDashboardClient(access_token=_jwt(), http_session=session)

    result = client.fetch_exposure("MU", "eod")

    assert result["levels"][-1] == {"key": "min_1d", "label": "1D Min", "value": None}
    assert result["complete"] is False


def test_expired_explicit_token_fails_before_network_call():
    session = _Session([])
    client = MenthorQDashboardClient(
        access_token=_jwt(expires_at=int(time.time()) - 10),
        http_session=session,
    )

    with pytest.raises(MenthorQDashboardAuthError, match="authentication is unavailable"):
        client.fetch_exposure("MU", "eod")

    assert session.calls == []


def test_provider_unauthorized_is_a_sanitized_auth_failure():
    session = _Session([_Response({}, status_code=401, text="expired private session")])
    client = MenthorQDashboardClient(access_token=_jwt(), http_session=session)

    with pytest.raises(MenthorQDashboardAuthError) as exc_info:
        client.fetch_exposure("MU", "eod")

    assert str(exc_info.value) == "dashboard authentication is unavailable"
    assert "private" not in str(exc_info.value)


def test_provider_request_timeout_is_sanitized():
    from clients.menthorq_dashboard_client import MenthorQDashboardTimeoutError

    client = MenthorQDashboardClient(access_token=_jwt(), http_session=_TimeoutSession())

    with pytest.raises(MenthorQDashboardTimeoutError) as exc_info:
        client.fetch_exposure("MU", "eod")

    assert str(exc_info.value) == "dashboard provider timed out"
    assert "private" not in str(exc_info.value)


def test_dedicated_storage_state_is_forced_to_owner_only(tmp_path: Path, monkeypatch):
    state_path = tmp_path / "menthorq_dashboard_storage_state.json"
    state_path.write_text(json.dumps({"cookies": [], "origins": []}))
    state_path.chmod(0o644)
    session = _Session([_Response(_exposure_payload()), _Response(_levels_payload())])
    client = MenthorQDashboardClient(storage_state_path=state_path, http_session=session)
    monkeypatch.setattr(client, "_token_from_storage_state", lambda: _jwt())

    client.fetch_exposure("MU", "eod")

    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


def test_missing_dedicated_state_bootstraps_through_wordpress_credentials(
    tmp_path: Path, monkeypatch
):
    token = _jwt()
    client = MenthorQDashboardClient(
        storage_state_path=tmp_path / "dashboard.json",
        username="operator@example.test",
        password="test-password",
    )
    calls: list[bool] = []
    monkeypatch.setattr(
        client,
        "_bootstrap_dashboard_session",
        lambda: calls.append(True) or token,
    )

    assert client._resolve_access_token() == token
    assert calls == [True]


def test_expired_dedicated_state_reboots_through_wordpress_credentials(
    tmp_path: Path, monkeypatch
):
    state_path = tmp_path / "dashboard.json"
    state_path.write_text(json.dumps({"cookies": [], "origins": []}))
    token = _jwt()
    client = MenthorQDashboardClient(
        storage_state_path=state_path,
        username="operator@example.test",
        password="test-password",
    )
    calls: list[bool] = []
    monkeypatch.setattr(
        client,
        "_token_from_storage_state",
        lambda: (_ for _ in ()).throw(MenthorQDashboardAuthError("unavailable")),
    )
    monkeypatch.setattr(
        client,
        "_bootstrap_dashboard_session",
        lambda: calls.append(True) or token,
    )

    assert client._resolve_access_token() == token
    assert calls == [True]


def test_missing_dedicated_state_without_wordpress_credentials_stays_sanitized(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.delenv("MENTHORQ_USER", raising=False)
    monkeypatch.delenv("MENTHORQ_PASS", raising=False)
    client = MenthorQDashboardClient(storage_state_path=tmp_path / "dashboard.json")

    with pytest.raises(MenthorQDashboardAuthError, match="authentication is unavailable"):
        client._resolve_access_token()


def test_upstream_error_never_echoes_response_body_or_token():
    token = _jwt()
    session = _Session(
        [_Response({}, status_code=500, text=f"debug Authorization: Bearer {token}")]
    )
    client = MenthorQDashboardClient(access_token=token, http_session=session)

    with pytest.raises(MenthorQDashboardUpstreamError) as exc_info:
        client.fetch_exposure("MU", "eod")

    assert token not in str(exc_info.value)
    assert "Authorization" not in str(exc_info.value)
