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


@pytest.fixture(autouse=True)
def reset_menthorq_auth_embargo():
    from clients import menthorq_dashboard_client as mod

    reset = getattr(mod, "_reset_auth_embargo_for_tests", None)
    if reset is not None:
        reset()
    yield
    if reset is not None:
        reset()


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


class TestExpiredJarFailsFast:
    """2026-08-07: an expired cookie jar cost ~28s per request — Playwright
    launched chromium to discover the jar was stale, then launched it AGAIN
    to attempt a re-login. The jar's own expiry metadata answers the first
    question for free, so a dead jar must skip straight to bootstrap (and,
    with no usable credentials, fail immediately)."""

    def _jar(self, tmp_path, expires_at):
        import json
        path = tmp_path / "jar.json"
        path.write_text(json.dumps({"cookies": [{
            "name": "cognito", "value": "x", "domain": ".menthorq.io",
            "path": "/", "expires": expires_at,
        }], "origins": []}))
        path.chmod(0o600)
        return path

    def test_expired_jar_never_launches_a_browser_to_read_it(self, tmp_path, monkeypatch):
        import time as _time
        from clients import menthorq_dashboard_client as mod

        # `username=""` is FALSY, so the constructor falls back to
        # MENTHORQ_USER/PASS from .env — which made this test perform a REAL
        # login. Blank the env so the credential path is genuinely absent.
        monkeypatch.setenv("MENTHORQ_USER", "")
        monkeypatch.setenv("MENTHORQ_PASS", "")
        monkeypatch.setenv("MENTHORQ_DASHBOARD_ACCESS_TOKEN", "")
        jar = self._jar(tmp_path, _time.time() - 86_400)
        client = mod.MenthorQDashboardClient(
            storage_state_path=jar, username="", password="",
        )
        monkeypatch.setattr(
            client, "_bootstrap_dashboard_session",
            lambda: (_ for _ in ()).throw(
                AssertionError("must not attempt a live login in a unit test")
            ),
        )
        called = {"storage": 0}

        def _boom():
            called["storage"] += 1
            raise AssertionError("launched a browser for a provably expired jar")

        monkeypatch.setattr(client, "_token_from_storage_state", _boom)
        with pytest.raises(mod.MenthorQDashboardAuthError):
            client._resolve_access_token()
        assert called["storage"] == 0

    def test_live_jar_still_reads_through_the_browser(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MENTHORQ_USER", "")
        monkeypatch.setenv("MENTHORQ_PASS", "")
        monkeypatch.setenv("MENTHORQ_DASHBOARD_ACCESS_TOKEN", "")
        import time as _time
        from clients import menthorq_dashboard_client as mod

        jar = self._jar(tmp_path, _time.time() + 7 * 86_400)
        client = mod.MenthorQDashboardClient(
            storage_state_path=jar, username="", password="",
        )
        monkeypatch.setattr(client, "_token_from_storage_state", lambda: "token-abc")
        assert client._resolve_access_token() == "token-abc"

    def test_jar_without_expiry_metadata_is_not_treated_as_expired(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MENTHORQ_USER", "")
        monkeypatch.setenv("MENTHORQ_PASS", "")
        monkeypatch.setenv("MENTHORQ_DASHBOARD_ACCESS_TOKEN", "")
        """Session cookies (expires = -1) carry no deadline — absence of
        metadata must never be read as expiry."""
        import json
        from clients import menthorq_dashboard_client as mod

        jar = tmp_path / "jar.json"
        jar.write_text(json.dumps({"cookies": [{
            "name": "cognito", "value": "x", "domain": ".menthorq.io",
            "path": "/", "expires": -1,
        }], "origins": []}))
        jar.chmod(0o600)
        client = mod.MenthorQDashboardClient(
            storage_state_path=jar, username="", password="",
        )
        monkeypatch.setattr(client, "_token_from_storage_state", lambda: "token-xyz")
        assert client._resolve_access_token() == "token-xyz"


class TestExpiredOverrideTokenFallsBack:
    """An EXPIRED operational override must not be a dead end.

    `_resolve_access_token` returns the env token untouched, then rejects it
    on expiry — WITHOUT trying the jar. So the 12h stopgap token installed
    on 2026-08-07 would have broken /options/net-gex again the moment it
    lapsed, even with a perfectly good self-refreshing jar on disk.
    """

    def test_expired_env_token_falls_back_to_the_jar(self, tmp_path, monkeypatch):
        import base64, json as _json, time as _time
        from clients import menthorq_dashboard_client as mod

        def _jwt(exp: float) -> str:
            body = base64.urlsafe_b64encode(_json.dumps({"exp": exp}).encode()).decode().rstrip("=")
            return f"header.{body}.sig"

        jar = tmp_path / "jar.json"
        jar.write_text(_json.dumps({"cookies": [{
            "name": "cognito", "value": "x", "domain": ".menthorq.io",
            "path": "/", "expires": _time.time() + 7 * 86_400,
        }], "origins": []}))
        jar.chmod(0o600)

        monkeypatch.setenv("MENTHORQ_DASHBOARD_ACCESS_TOKEN", _jwt(_time.time() - 60))
        client = mod.MenthorQDashboardClient(
            storage_state_path=jar, username="u", password="p",
        )
        monkeypatch.setattr(client, "_token_from_storage_state", lambda: _jwt(_time.time() + 3600))
        monkeypatch.setattr(
            client, "_bootstrap_dashboard_session",
            lambda: (_ for _ in ()).throw(AssertionError("jar was usable; must not re-login")),
        )
        assert client._resolve_access_token().startswith("header.")

    def test_live_env_token_is_still_preferred(self, tmp_path, monkeypatch):
        import base64, json as _json, time as _time
        from clients import menthorq_dashboard_client as mod

        body = base64.urlsafe_b64encode(
            _json.dumps({"exp": _time.time() + 3600}).encode()
        ).decode().rstrip("=")
        live = f"header.{body}.sig"
        monkeypatch.setenv("MENTHORQ_DASHBOARD_ACCESS_TOKEN", live)
        client = mod.MenthorQDashboardClient(
            storage_state_path=tmp_path / "absent.json",
            username="u", password="p",
        )
        monkeypatch.setattr(
            client, "_bootstrap_dashboard_session",
            lambda: (_ for _ in ()).throw(AssertionError("must not log in with a live token")),
        )
        assert client._resolve_access_token() == live


def test_request_path_auth_budget_fits_inside_next_proxy():
    """2026-08-20: DEFAULT_LOGIN_TIMEOUT_SECONDS was 60s and Next.js
    OPTIONS_PROXY_TIMEOUT_MS is 50s, so a broken MenthorQ re-login 504'd
    the browser while the 90s login-probe still saw FastAPI's 503."""
    from clients import menthorq_dashboard_client as mod

    assert mod.DEFAULT_LOGIN_TIMEOUT_SECONDS <= 25
    assert mod.REQUEST_PATH_AUTH_BUDGET_SECONDS < 50
    assert mod.DEFAULT_LOGIN_TIMEOUT_SECONDS <= mod.REQUEST_PATH_AUTH_BUDGET_SECONDS


def _live_dashboard_jar(tmp_path: Path) -> Path:
    path = tmp_path / "jar.json"
    path.write_text(json.dumps({"cookies": [{
        "name": "cognito", "value": "x", "domain": ".menthorq.io",
        "path": "/", "expires": time.time() + 7 * 86_400,
    }], "origins": []}))
    path.chmod(0o600)
    return path


class TestAuthFailureEmbargo:
    """A live-looking jar that cannot mint still paid chromium + 60s
    bootstrap on every /options/net-gex click. FastAPI constructs a new
    client per request, so the embargo must be process-wide."""

    def test_second_resolve_skips_bootstrap_during_embargo(self, tmp_path, monkeypatch):
        from clients import menthorq_dashboard_client as mod

        monkeypatch.setenv("MENTHORQ_USER", "")
        monkeypatch.setenv("MENTHORQ_PASS", "")
        monkeypatch.setenv("MENTHORQ_DASHBOARD_ACCESS_TOKEN", "")
        jar = _live_dashboard_jar(tmp_path)
        client = mod.MenthorQDashboardClient(
            storage_state_path=jar, username="u", password="p",
        )
        calls = {"bootstrap": 0, "mint": 0}

        def _mint():
            calls["mint"] += 1
            raise mod.MenthorQDashboardAuthError("dashboard authentication is unavailable")

        def _bootstrap():
            calls["bootstrap"] += 1
            raise mod.MenthorQDashboardAuthError("dashboard authentication is unavailable")

        monkeypatch.setattr(client, "_token_from_storage_state", _mint)
        monkeypatch.setattr(client, "_bootstrap_dashboard_session", _bootstrap)

        with pytest.raises(mod.MenthorQDashboardAuthError):
            client._resolve_access_token()
        assert calls == {"bootstrap": 1, "mint": 1}

        later = mod.MenthorQDashboardClient(
            storage_state_path=jar, username="u", password="p",
        )
        monkeypatch.setattr(later, "_token_from_storage_state", _mint)
        monkeypatch.setattr(later, "_bootstrap_dashboard_session", _bootstrap)
        with pytest.raises(mod.MenthorQDashboardAuthError):
            later._resolve_access_token()
        assert calls == {"bootstrap": 1, "mint": 1}

    def test_unspendable_live_jar_still_bootstraps_once(self, tmp_path, monkeypatch):
        from clients import menthorq_dashboard_client as mod

        monkeypatch.setenv("MENTHORQ_DASHBOARD_ACCESS_TOKEN", "")
        jar = _live_dashboard_jar(tmp_path)
        token = _jwt()
        client = mod.MenthorQDashboardClient(
            storage_state_path=jar, username="u", password="p",
        )
        calls = {"bootstrap": 0}

        def _mint():
            raise mod.MenthorQDashboardAuthError("dashboard authentication is unavailable")

        def _bootstrap():
            calls["bootstrap"] += 1
            return token

        monkeypatch.setattr(client, "_token_from_storage_state", _mint)
        monkeypatch.setattr(client, "_bootstrap_dashboard_session", _bootstrap)
        assert client._resolve_access_token() == token
        assert calls["bootstrap"] == 1
