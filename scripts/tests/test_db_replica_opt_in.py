"""The libsql embedded replica cannot be opened from any environment.

Embedded replicas were retired 2026-05-20 after WAL conflicts between
multi-writer hosts (feedback_libsql_replica_one_writer.md). DUR-07 made the
replica opt-in via RADON_DB_USE_REPLICA=1; the opt-in itself is now removed,
so every knob combination below must still produce one direct-to-cloud
connection with no ``sync_url``, no back-fill ``sync()`` and no warning.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_FAKE_URL = "libsql://example.invalid"
_FAKE_TOKEN = "fake-token"


class _FakeConn:
    def __init__(self) -> None:
        self.sync_calls = 0

    def sync(self) -> None:
        self.sync_calls += 1


def _fresh_client_module():
    import db.client as client_mod  # noqa: WPS433

    importlib.reload(client_mod)
    return client_mod


def _arm(monkeypatch: pytest.MonkeyPatch, client_mod, connect_calls: list):
    """Fake out libsql.connect + give the module clean TURSO creds.

    PYTEST_CURRENT_TEST is removed so nothing pytest-specific can mask the
    env-flag behavior (the poisoning guard is covered separately in
    test_db_client_pytest_guard.py). Removing it also removes the only reason
    the production-writer guard lets a non-Linux host through, so the
    platform is pinned to the production one.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("TURSO_DB_URL", _FAKE_URL)
    monkeypatch.setenv("TURSO_AUTH_TOKEN", _FAKE_TOKEN)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("RADON_DB_USE_REPLICA", raising=False)
    monkeypatch.delenv("RADON_DB_NO_REPLICA", raising=False)

    def fake_connect(*args, **kwargs):
        conn = _FakeConn()
        connect_calls.append((args, kwargs, conn))
        return conn

    monkeypatch.setattr(client_mod.libsql, "connect", fake_connect)


@pytest.mark.parametrize("env", [
    {},
    {"RADON_DB_USE_REPLICA": "1"},
    {"RADON_DB_USE_REPLICA": "1", "RADON_DB_NO_REPLICA": "1"},
    {"RADON_DB_NO_REPLICA": "1"},
], ids=["clean", "opt-in", "opt-in+kill-switch", "kill-switch"])
def test_every_env_opens_one_direct_cloud_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, env: dict
):
    client_mod = _fresh_client_module()
    connect_calls: list = []
    _arm(monkeypatch, client_mod, connect_calls)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    client_mod.get_db()

    assert len(connect_calls) == 1
    args, kwargs, conn = connect_calls[0]
    assert args[0] == _FAKE_URL, "expected a direct-to-cloud connection"
    assert "sync_url" not in kwargs, "an embedded replica was opened"
    assert conn.sync_calls == 0, "a replica back-fill sync() ran"
    assert "replica" not in capsys.readouterr().err.lower()
