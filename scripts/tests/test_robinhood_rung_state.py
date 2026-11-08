"""Robinhood rung: one tri-state, a persisted disable, recoverable rotations.

Audit implicit-state-machines-20261103 §3.4 "Robinhood rung":
  - the refresh-disabled state lived in memory, so every oneshot re-spent a
    rejected grant against the token endpoint;
  - the rotation fallback written to the temp dir was never read back;
  - the env overlay resurrected a stale refresh token over an existing file;
  - robinhood_configured() said False both when unconfigured and when the
    rung was merely closed by the breaker.
"""
from __future__ import annotations

import json
import os
import time

import pytest
from clients import robinhood_client as rh
from clients.robinhood_client import RobinhoodTokenStore

from tests.test_robinhood_client import _FakeResponse, _write_token_file  # type: ignore


@pytest.fixture(autouse=True)
def isolated_token_state(monkeypatch, tmp_path):
    """No env credentials, a tmp token file, every process machine reset."""
    rh._reset_process_state()
    monkeypatch.setenv("ROBINHOOD_MCP_TOKEN_FILE", str(tmp_path / "rh-mcp.json"))
    for name in ("ROBINHOOD_MCP_TOKEN", "ROBINHOOD_MCP_REFRESH_TOKEN",
                 "ROBINHOOD_MCP_CLIENT_ID", "ROBINHOOD_MCP_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rh, "ROTATED_TOKEN_FALLBACK_DIR", tmp_path / "rotated")
    yield tmp_path / "rh-mcp.json"
    rh._reset_process_state()


def _endpoint(monkeypatch, calls, *, status=200, text=""):
    payload = {"access_token": "fresh-access", "token_type": "Bearer",
               "expires_in": 259200, "refresh_token": "refresh-2"}

    def post(url, data=None, headers=None, timeout=None, **_kw):
        calls.append(dict(data or {}))
        if status >= 400:
            return _FakeResponse(status_code=status, text=text or "{}")
        return _FakeResponse(payload=payload)

    monkeypatch.setattr(rh.requests, "post", post)


def _new_process():
    """A fresh oneshot: every in-memory machine is reset."""
    rh._reset_process_state()


class TestTriState:
    def test_unconfigured(self):
        assert rh.robinhood_rung_state() == "unconfigured"
        assert rh.robinhood_configured() is False

    def test_open(self, isolated_token_state):
        _write_token_file(isolated_token_state, refresh_token="r", client_id="cid")
        assert rh.robinhood_rung_state() == "open"
        assert rh.robinhood_configured() is True

    def test_breaker_is_closed_not_unconfigured(self, isolated_token_state):
        _write_token_file(isolated_token_state, refresh_token="r", client_id="cid")
        rh._open_breaker("MCP unreachable", rh.FAILURE_NETWORK)
        assert rh.robinhood_rung_state() == "closed"
        assert rh.robinhood_configured() is False


class TestPersistedDisable:
    def test_rejected_grant_stays_disabled_across_processes(self, monkeypatch, isolated_token_state):
        _write_token_file(isolated_token_state, refresh_token="refresh-dead", client_id="cid")
        calls: list = []
        _endpoint(monkeypatch, calls, status=400, text='{"error": "invalid_grant"}')
        assert rh.fetch_robinhood_closes(["SPY"]) == {}
        assert len(calls) == 1

        _new_process()
        assert rh.robinhood_rung_state() == "closed"
        assert rh.fetch_robinhood_closes(["SPY"]) == {}
        with pytest.raises(rh.RobinhoodNotConfiguredError):
            RobinhoodTokenStore().refresh()
        assert len(calls) == 1, "the next oneshot must not re-spend a rejected grant"
        marker = isolated_token_state.with_name(isolated_token_state.name + ".disabled")
        assert "refresh-dead" not in marker.read_text(), "no credential in the marker"

    def test_reprovisioned_credentials_reopen_the_rung(self, monkeypatch, isolated_token_state):
        _write_token_file(isolated_token_state, refresh_token="refresh-dead", client_id="cid")
        _endpoint(monkeypatch, [], status=400, text='{"error": "invalid_grant"}')
        rh.fetch_robinhood_closes(["SPY"])
        _new_process()
        _write_token_file(isolated_token_state, refresh_token="refresh-new", client_id="cid")
        assert rh.robinhood_rung_state() == "open"

    def test_disable_is_bounded_in_time(self, monkeypatch, isolated_token_state):
        _write_token_file(isolated_token_state, refresh_token="refresh-dead", client_id="cid")
        _endpoint(monkeypatch, [], status=400, text='{"error": "invalid_grant"}')
        rh.fetch_robinhood_closes(["SPY"])
        _new_process()
        later = time.time() + rh.DISABLED_MARKER_TTL_S + 1
        monkeypatch.setattr(rh.time, "time", lambda: later)
        assert rh.robinhood_rung_state() == "open"


class TestRotationFallback:
    def _fail_commit(self, monkeypatch, target):
        real_replace = os.replace

        def failing(src, dst, *a, **k):
            if str(dst) == str(target):
                raise PermissionError(1, "Operation not permitted", str(dst))
            return real_replace(src, dst, *a, **k)

        monkeypatch.setattr(rh.os, "replace", failing)
        return real_replace

    def test_next_process_reads_the_rotation_back(self, monkeypatch, isolated_token_state, tmp_path):
        _write_token_file(isolated_token_state, access_token="stale", refresh_token="refresh-1",
                          client_id="cid", expires_at=time.time() - 10)
        fallback = tmp_path / "fallback"
        fallback.mkdir()
        monkeypatch.setattr(rh, "ROTATED_TOKEN_FALLBACK_DIR", fallback)
        calls: list = []
        _endpoint(monkeypatch, calls)
        real_replace = self._fail_commit(monkeypatch, isolated_token_state)
        with pytest.raises(rh.RobinhoodClientError):
            RobinhoodTokenStore().refresh()
        monkeypatch.setattr(rh.os, "replace", real_replace)

        _new_process()
        store = RobinhoodTokenStore()
        assert store.refresh_token == "refresh-2", "the spent refresh token must not be reused"
        assert store.access_token == "fresh-access"

    def test_a_successful_persist_retires_the_fallback(self, monkeypatch, isolated_token_state, tmp_path):
        _write_token_file(isolated_token_state, access_token="stale", refresh_token="refresh-1",
                          client_id="cid", expires_at=time.time() - 10)
        fallback = tmp_path / "fallback"
        fallback.mkdir()
        monkeypatch.setattr(rh, "ROTATED_TOKEN_FALLBACK_DIR", fallback)
        _endpoint(monkeypatch, [])
        real_replace = self._fail_commit(monkeypatch, isolated_token_state)
        with pytest.raises(rh.RobinhoodClientError):
            RobinhoodTokenStore().refresh()
        monkeypatch.setattr(rh.os, "replace", real_replace)
        _new_process()
        store = RobinhoodTokenStore()
        store.persist()
        assert list(fallback.glob("*.json")) == []
        assert json.loads(isolated_token_state.read_text())["refresh_token"] == "refresh-2"


class TestEnvOverlay:
    def test_env_refresh_token_does_not_override_an_existing_file(self, monkeypatch, isolated_token_state):
        _write_token_file(isolated_token_state, access_token="file-access", client_id="cid")
        monkeypatch.setenv("ROBINHOOD_MCP_REFRESH_TOKEN", "env-stale-refresh")
        assert RobinhoodTokenStore().refresh_token == ""

    def test_env_does_not_resurrect_over_a_corrupt_file(self, monkeypatch, isolated_token_state):
        isolated_token_state.write_text("{torn")
        monkeypatch.setenv("ROBINHOOD_MCP_TOKEN", "env-stale-access")
        monkeypatch.setenv("ROBINHOOD_MCP_REFRESH_TOKEN", "env-stale-refresh")
        store = RobinhoodTokenStore()
        assert store.refresh_token == "" and store.access_token == ""

    def test_env_client_id_still_fills_a_missing_field(self, monkeypatch, isolated_token_state):
        _write_token_file(isolated_token_state, refresh_token="r")
        monkeypatch.setenv("ROBINHOOD_MCP_CLIENT_ID", "cid-public")
        assert RobinhoodTokenStore().client_id == "cid-public"
