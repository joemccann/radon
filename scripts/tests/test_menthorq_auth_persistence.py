"""MenthorQ auth embargo survives a restart; the session jar is written atomically.

Audit implicit-state-machines-20261103 §3.4 "MenthorQ auth embargo": the
embargo lived in process memory (a restarted oneshot paid chromium + bootstrap
again on a jar that cannot mint) and the storage-state write was not atomic.
"""
from __future__ import annotations

import json
import pathlib
import stat
import time

import pytest
from clients import menthorq_dashboard_client as mod

from tests.test_menthorq_client import mock_env_creds, mock_playwright  # noqa: F401  # type: ignore
from tests.test_menthorq_dashboard_client import _live_dashboard_jar  # type: ignore


def _failing_client(jar, calls):
    client = mod.MenthorQDashboardClient(storage_state_path=jar, username="u", password="secret-pass")

    def _fail():
        calls.append(1)
        raise mod.MenthorQDashboardAuthError("dashboard authentication is unavailable")

    client._token_from_storage_state = _fail
    client._bootstrap_dashboard_session = _fail
    return client


@pytest.fixture
def jar(tmp_path, monkeypatch):
    for name in ("MENTHORQ_USER", "MENTHORQ_PASS", "MENTHORQ_DASHBOARD_ACCESS_TOKEN"):
        monkeypatch.setenv(name, "")
    return _live_dashboard_jar(tmp_path)


class TestPersistedEmbargo:
    def test_restart_does_not_rebootstrap_inside_the_embargo(self, jar):
        calls: list = []
        with pytest.raises(mod.MenthorQDashboardAuthError):
            _failing_client(jar, calls)._resolve_access_token()
        attempts = len(calls)
        mod._reset_auth_embargo_for_tests()  # a new process: memory is gone
        with pytest.raises(mod.MenthorQDashboardAuthEmbargoed):
            _failing_client(jar, calls)._resolve_access_token()
        assert len(calls) == attempts

    def test_persisted_embargo_lapses(self, jar, monkeypatch):
        calls: list = []
        with pytest.raises(mod.MenthorQDashboardAuthError):
            _failing_client(jar, calls)._resolve_access_token()
        mod._reset_auth_embargo_for_tests()
        later = time.time() + mod._AUTH_FAILURE_EMBARGO_SECONDS + 1
        monkeypatch.setattr(mod.time, "time", lambda: later)
        attempts = len(calls)
        with pytest.raises(mod.MenthorQDashboardAuthError) as raised:
            _failing_client(jar, calls)._resolve_access_token()
        assert not isinstance(raised.value, mod.MenthorQDashboardAuthEmbargoed)
        assert len(calls) > attempts

    def test_a_refreshed_jar_is_not_embargoed(self, jar):
        calls: list = []
        with pytest.raises(mod.MenthorQDashboardAuthError):
            _failing_client(jar, calls)._resolve_access_token()
        mod._reset_auth_embargo_for_tests()
        jar.write_text(jar.read_text() + " ")  # operator re-seeded the session
        jar.chmod(0o600)
        with pytest.raises(mod.MenthorQDashboardAuthError) as raised:
            _failing_client(jar, calls)._resolve_access_token()
        assert not isinstance(raised.value, mod.MenthorQDashboardAuthEmbargoed)

    def test_the_sidecar_holds_no_credentials(self, jar):
        with pytest.raises(mod.MenthorQDashboardAuthError):
            _failing_client(jar, [])._resolve_access_token()
        sidecars = list(jar.parent.glob("*auth-embargo*"))
        assert len(sidecars) == 1
        assert "secret-pass" not in sidecars[0].read_text()
        assert stat.S_IMODE(sidecars[0].stat().st_mode) == 0o600


@pytest.mark.usefixtures("mock_env_creds")
def test_menthorq_storage_state_write_is_atomic(mock_playwright, tmp_path):  # noqa: F811
    from clients.menthorq_client import MenthorQClient

    ss_path = tmp_path / "menthorq_cache" / "menthorq_storage_state.json"
    client = MenthorQClient(storage_state_path=ss_path)
    ss_path.write_text(json.dumps({"cookies": ["good"]}))
    ss_path.chmod(0o600)

    def torn(path):
        pathlib.Path(path).write_text('{"cook')
        raise RuntimeError("browser died mid-write")

    mock_playwright["context"].storage_state.side_effect = torn
    client.persist_storage_state()
    assert json.loads(ss_path.read_text()) == {"cookies": ["good"]}
    assert list(ss_path.parent.glob("*.tmp")) == []
