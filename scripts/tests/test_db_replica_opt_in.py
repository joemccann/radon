"""DUR-07 — the libsql embedded replica is opt-in ONLY.

Embedded replicas were retired 2026-05-20 after WAL conflicts between
multi-writer hosts (feedback_libsql_replica_one_writer.md). The old code
defaulted to the replica unless every entrypoint remembered to set
RADON_DB_NO_REPLICA=1 — the one unit that missed it (ib-watchdog)
resurrected a 1.36GB data/replica.db and hung for 6h on 2026-06-10.

These tests pin the inverted default in `db.client.get_db()`:

  * clean env            → direct cloud connection (no replica path)
  * RADON_DB_USE_REPLICA=1 → replica branch, with a loud stderr warning
  * legacy RADON_DB_NO_REPLICA=1 still forces OFF even when both are set
  * pytest can never take the replica branch regardless of flags
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

    PYTEST_CURRENT_TEST is removed so the assertions exercise the env-flag
    logic itself, not the pytest poisoning guard (covered separately in
    test_db_client_pytest_guard.py).
    """
    monkeypatch.setenv("TURSO_DB_URL", _FAKE_URL)
    monkeypatch.setenv("TURSO_AUTH_TOKEN", _FAKE_TOKEN)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("RADON_DB_USE_REPLICA", raising=False)
    monkeypatch.delenv("RADON_DB_NO_REPLICA", raising=False)

    def fake_connect(*args, **kwargs):
        connect_calls.append((args, kwargs))
        return _FakeConn()

    monkeypatch.setattr(client_mod.libsql, "connect", fake_connect)


def _assert_direct_cloud(connect_calls: list) -> None:
    assert len(connect_calls) == 1
    args, kwargs = connect_calls[0]
    assert args[0] == _FAKE_URL, "expected a direct-to-cloud connection"
    assert "sync_url" not in kwargs, "replica path taken without opt-in"


class TestReplicaOptInDefault:
    def test_clean_env_defaults_to_direct_cloud(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        client_mod = _fresh_client_module()
        connect_calls: list = []
        _arm(monkeypatch, client_mod, connect_calls)

        client_mod.get_db()

        _assert_direct_cloud(connect_calls)

    def test_use_replica_opts_in_and_warns(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ):
        client_mod = _fresh_client_module()
        connect_calls: list = []
        _arm(monkeypatch, client_mod, connect_calls)
        monkeypatch.setenv("RADON_DB_USE_REPLICA", "1")

        conn = client_mod.get_db()

        assert len(connect_calls) == 1
        args, kwargs = connect_calls[0]
        assert args[0].endswith("replica.db"), "opt-in must take the replica path"
        assert kwargs.get("sync_url") == _FAKE_URL
        assert conn.sync_calls == 1, "replica path must back-fill on first open"
        stderr = capsys.readouterr().err
        assert "RADON_DB_USE_REPLICA" in stderr, (
            "opting in must log a loud warning so the choice is visible "
            "in journalctl"
        )

    def test_legacy_no_replica_forces_off_even_with_opt_in(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        client_mod = _fresh_client_module()
        connect_calls: list = []
        _arm(monkeypatch, client_mod, connect_calls)
        monkeypatch.setenv("RADON_DB_USE_REPLICA", "1")
        monkeypatch.setenv("RADON_DB_NO_REPLICA", "1")

        client_mod.get_db()

        _assert_direct_cloud(connect_calls)

    def test_pytest_never_takes_replica_branch(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Even an explicit opt-in must not open a replica under pytest
        (PYTEST_CURRENT_TEST is left in place; the poisoning guard is
        bypassed with the documented override)."""
        client_mod = _fresh_client_module()
        connect_calls: list = []
        _arm(monkeypatch, client_mod, connect_calls)
        monkeypatch.setenv(
            "PYTEST_CURRENT_TEST", "test_db_replica_opt_in (call)"
        )
        monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")
        monkeypatch.setenv("RADON_DB_USE_REPLICA", "1")

        client_mod.get_db()

        _assert_direct_cloud(connect_calls)
