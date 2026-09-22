"""Offline adversarial tests: no real credentials, browser or provider calls."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from clients import menthorq_dashboard_client as mq
from test_menthorq_dashboard_client import _Response, _Session, _exposure_payload, _levels_payload
from test_menthorq_dashboard_bootstrap import _install_fake_playwright, LIVE_SESSION


@pytest.fixture(autouse=True)
def isolated_auth(monkeypatch):
    for name in ("MENTHORQ_DASHBOARD_ACCESS_TOKEN", "MENTHORQ_USER", "MENTHORQ_PASS"):
        monkeypatch.delenv(name, raising=False)
    mq._reset_auth_embargo_for_tests()
    yield
    mq._reset_auth_embargo_for_tests()


def write_jar(path, *, cognito=-60, durable=3600):
    path.write_text(json.dumps({"cookies": [
        {"name": "cognito", "expires": time.time() + cognito},
        {"name": "__Secure-authjs.session-token.0", "expires": time.time() + durable},
        {"name": "__Secure-authjs.session-token.1", "expires": time.time() + durable},
    ], "origins": []}))


def test_expired_ephemeral_cookie_does_not_discard_live_dashboard_session(tmp_path, monkeypatch):
    path = tmp_path / "jar.json"
    write_jar(path)
    client = mq.MenthorQDashboardClient(storage_state_path=path, username="u", password="p")
    exchange = Mock(return_value="live-session-token")
    bootstrap = Mock(side_effect=AssertionError("must spend durable session first"))
    monkeypatch.setattr(client, "_token_from_storage_state", exchange)
    monkeypatch.setattr(client, "_bootstrap_dashboard_session", bootstrap)
    assert client._resolve_access_token() == "live-session-token"
    exchange.assert_called_once()
    bootstrap.assert_not_called()


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("rejected_leg", ["cube", "levels"])
def test_rejected_ambient_token_recovers_once_and_refetches_both_legs(
    tmp_path, monkeypatch, status, rejected_leg,
):
    monkeypatch.setenv("MENTHORQ_DASHBOARD_ACCESS_TOKEN", "revoked-override")
    path = tmp_path / "jar.json"
    write_jar(path)
    responses = [] if rejected_leg == "cube" else [_Response(_exposure_payload())]
    session = _Session(responses + [
        _Response({}, status_code=status),
        _Response(_exposure_payload()), _Response(_levels_payload()),
    ])
    client = mq.MenthorQDashboardClient(storage_state_path=path, http_session=session)
    monkeypatch.setattr(client, "_token_from_storage_state", Mock(return_value="renewed-token"))
    assert client.fetch_exposure("MU")["symbol"] == "MU"
    assert [c["headers"]["Authorization"] for c in session.calls[-2:]] == [
        "Bearer renewed-token", "Bearer renewed-token",
    ]
    assert len(session.calls) == (3 if rejected_leg == "cube" else 4)


def test_rejected_session_token_requires_bootstrap_not_same_token_replay(tmp_path, monkeypatch):
    path = tmp_path / "jar.json"
    write_jar(path, cognito=3600)
    client = mq.MenthorQDashboardClient(
        storage_state_path=path, username="u", password="p",
        http_session=_Session([_Response({}, status_code=401),
                               _Response(_exposure_payload()), _Response(_levels_payload())]),
    )
    monkeypatch.setattr(client, "_token_from_storage_state", Mock(return_value="revoked-session"))
    bootstrap = Mock(return_value="fresh-session")
    monkeypatch.setattr(client, "_bootstrap_dashboard_session", bootstrap)
    assert client.fetch_exposure("MU")["symbol"] == "MU"
    bootstrap.assert_called_once()


def test_repeated_rejection_is_bounded_and_never_echoes_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("MENTHORQ_DASHBOARD_ACCESS_TOKEN", "revoked-private-token")
    path = tmp_path / "jar.json"
    write_jar(path, cognito=3600)
    session = _Session([_Response({}, status_code=401), _Response({}, status_code=403)])
    client = mq.MenthorQDashboardClient(storage_state_path=path, http_session=session)
    exchange = Mock(return_value="new-private-token")
    monkeypatch.setattr(client, "_token_from_storage_state", exchange)
    with pytest.raises(mq.MenthorQDashboardAuthError) as exc:
        client.fetch_exposure("MU")
    assert len(session.calls) == 2
    exchange.assert_called_once()
    assert "private-token" not in str(exc.value)
    assert mq._auth_embargo_active()
    later = mq.MenthorQDashboardClient(storage_state_path=path, http_session=_Session([]))
    with pytest.raises(mq.MenthorQDashboardAuthEmbargoed):
        later.fetch_exposure("MU")


def test_replaced_jar_clears_failure_embargo_without_service_restart(tmp_path, monkeypatch):
    path = tmp_path / "jar.json"
    path.write_text("{}")
    client = mq.MenthorQDashboardClient(storage_state_path=path)
    monkeypatch.setattr(client, "_token_from_storage_state", Mock(side_effect=mq.MenthorQDashboardAuthError()))
    with pytest.raises(mq.MenthorQDashboardAuthError):
        client._resolve_access_token()
    replacement = tmp_path / "replacement"
    write_jar(replacement, cognito=3600)
    replacement.replace(path)
    later = mq.MenthorQDashboardClient(storage_state_path=path)
    monkeypatch.setattr(later, "_token_from_storage_state", Mock(return_value="repaired-token"))
    assert later._resolve_access_token() == "repaired-token"


def test_concurrent_cold_requests_bootstrap_once(tmp_path, monkeypatch):
    path = tmp_path / "jar.json"
    calls = []

    def bootstrap(self):
        calls.append(1)
        time.sleep(0.04)
        write_jar(path, cognito=3600)
        return "renewed"

    monkeypatch.setattr(mq.MenthorQDashboardClient, "_bootstrap_dashboard_session", bootstrap)
    monkeypatch.setattr(mq.MenthorQDashboardClient, "_token_from_storage_state", lambda self: "renewed")
    clients = [mq.MenthorQDashboardClient(storage_state_path=path, username="u", password="p") for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(lambda c: c._resolve_access_token(), clients)) == ["renewed"] * 8
    assert len(calls) == 1


def test_failed_atomic_replace_keeps_previous_private_jar(tmp_path, monkeypatch):
    path = tmp_path / "jar.json"
    original = '{"cookies": [], "origins": [], "previous": true}'
    path.write_text(original)
    path.chmod(0o600)
    client = mq.MenthorQDashboardClient(storage_state_path=path)

    class Context:
        def storage_state(self, path=None):
            state = {"cookies": [], "origins": []}
            if path:
                Path(path).write_text(json.dumps(state))
            return state

    monkeypatch.setattr(mq.os, "replace", Mock(side_effect=OSError("disk failure")))
    with pytest.raises(mq.MenthorQDashboardStorageError):
        client._persist_dashboard_storage_state(Context())
    assert path.read_text() == original
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("method", ["_token_from_storage_state", "_bootstrap_dashboard_session"])
def test_storage_failure_is_not_misclassified_as_bad_credentials(tmp_path, monkeypatch, method):
    _install_fake_playwright(
        monkeypatch, post_submit_url="https://dashboard.menthorq.io/en/options/exposure",
        payload=LIVE_SESSION,
    )
    client = mq.MenthorQDashboardClient(storage_state_path=tmp_path / "jar.json", username="u", password="p")
    monkeypatch.setattr(client, "_persist_dashboard_storage_state",
                        Mock(side_effect=mq.MenthorQDashboardStorageError("local storage fault")))
    with pytest.raises(mq.MenthorQDashboardStorageError):
        getattr(client, method)()


def test_explicit_bad_token_cannot_embargo_a_healthy_client(tmp_path, monkeypatch):
    from test_menthorq_dashboard_client import _jwt
    client = mq.MenthorQDashboardClient(access_token=_jwt(expires_at=int(time.time()) - 1))
    with pytest.raises(mq.MenthorQDashboardAuthError):
        client._resolve_access_token()
    assert not mq._auth_embargo_active()
    later = mq.MenthorQDashboardClient(storage_state_path=tmp_path / "jar.json", username="u", password="p")
    monkeypatch.setattr(later, "_bootstrap_dashboard_session", lambda: "good-token")
    assert later._resolve_access_token() == "good-token"


def test_submillisecond_playwright_budget_never_becomes_unbounded():
    client = mq.MenthorQDashboardClient()
    assert client._remaining_ms(time.monotonic() + 0.0009, 20) == 1


def test_data_requests_share_remaining_budget_after_slow_auth(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(mq.time, "monotonic", lambda: clock[0])
    client = mq.MenthorQDashboardClient()

    def resolve():
        clock[0] += 39
        return "token"

    calls = []

    class Session:
        def get(self, url, **kwargs):
            calls.append(kwargs["timeout"])
            clock[0] += 6
            return _Response(_exposure_payload())

    monkeypatch.setattr(client, "_resolve_access_token", resolve)
    client._http = Session()
    with pytest.raises(mq.MenthorQDashboardTimeoutError):
        client.fetch_exposure("MU")
    assert calls == [6.0]
    assert client._request_deadline is None


def test_queue_admission_does_not_launch_browser_after_budget(tmp_path, monkeypatch):
    client = mq.MenthorQDashboardClient(storage_state_path=tmp_path / "jar.json")
    client._auth_deadline = time.monotonic() + 0.01
    resolve = Mock(side_effect=AssertionError("must not resolve after queue timeout"))
    monkeypatch.setattr(client, "_resolve_access_token_uncached", resolve)
    with mq._auth_resolution_lock:
        with pytest.raises(mq.MenthorQDashboardTimeoutError):
            client._resolve_access_token()
    resolve.assert_not_called()


@pytest.mark.parametrize("payload", [[], None, "invalid", 123])
def test_nonobject_jwt_payload_cannot_crash_resolution(payload):
    import base64
    token = "h." + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode() + ".s"
    assert mq._jwt_expiry(token) is None


def test_durable_session_cookie_without_expiry_does_not_inherit_cognito_expiry(tmp_path):
    path = tmp_path / "jar.json"
    path.write_text(json.dumps({"cookies": [
        {"name": "cognito", "expires": time.time() - 60},
        {"name": "__Secure-authjs.session-token", "expires": -1},
    ]}))
    assert not mq._storage_state_expired(path)


@pytest.mark.parametrize("change", [
    lambda p: p.update(ticker="WRONG"),
    lambda p: p.update(timestamp=""),
    lambda p: p.pop("spot_price"),
    lambda p: p.update(strikes=[]),
    lambda p: p.update(expirations=[]),
    lambda p: p["expirations"].__setitem__(0, None),
    lambda p: p["expirations"][0].update(expiration_date="bad"),
    lambda p: p["expirations"][0].update(dte=-1),
    lambda p: p.update(cells=[]),
    lambda p: p["cells"].pop("net_gex"),
    lambda p: p["cells"]["strike_idx"].__setitem__(0, True),
    lambda p: p["cells"]["expiration_idx"].__setitem__(0, 0.5),
    lambda p: p["cells"]["strike_idx"].__setitem__(1, 0) or p["cells"]["expiration_idx"].__setitem__(1, 0),
    lambda p: p["cells"]["abs_dex"].__setitem__(0, -1),
])
def test_partial_spot_never_relaxes_cube_integrity(change):
    payload = _exposure_payload()
    payload["spot_price"] = None
    change(payload)
    with pytest.raises(mq.MenthorQDashboardPayloadError):
        mq.MenthorQDashboardClient._normalize("MU", "eod", payload, _levels_payload())


@pytest.mark.parametrize("payload", [[], None, "invalid"])
def test_gateway_nonobject_response_stays_sanitized(payload):
    client = mq.MenthorQDashboardClient(
        access_token="explicit", http_session=_Session([_Response(payload)]),
    )
    with pytest.raises(mq.MenthorQDashboardPayloadError):
        client.fetch_exposure("MU")


def test_gateway_transport_error_never_starts_login(monkeypatch):
    import requests
    client = mq.MenthorQDashboardClient(
        http_session=Mock(get=Mock(side_effect=requests.ConnectionError("private transport"))),
    )
    monkeypatch.setattr(client, "_resolve_access_token", lambda: "token")
    bootstrap = Mock()
    monkeypatch.setattr(client, "_bootstrap_dashboard_session", bootstrap)
    with pytest.raises(mq.MenthorQDashboardUpstreamError) as exc:
        client.fetch_exposure("MU")
    assert "private" not in str(exc.value)
    bootstrap.assert_not_called()
