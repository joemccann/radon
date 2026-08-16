"""A corrupt daemon_state.json must not forget an active Flex embargo.

Plan item C18 (docs/cash-flow-sync-overhaul.md).

`daemon.load_state()` treats ANY `verified_load` exception as "start
blank". `data/daemon_state.json` is checksummed and covers every handler,
so one bad write forgets `cash_flow_sync`'s circuit breaker entirely — a
live 72h embargo becomes no embargo, and the next 17:00 ET window
re-probes a token that IBKR still considers hot. On a sliding-window rate
limit, that probe extends the block instead of clearing it.

`service_health.last_error.next_attempt_at` is the same value written by
`_record_failure` on every failed cycle, so it is a faithful and already
persisted lower bound. Seeding from it is strictly more conservative than
starting blank, and it is a read, never a write.

⛔ No Flex requests, no Turso: the service_health read is stubbed.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from monitor_daemon.daemon import MonitorDaemon
from monitor_daemon.handlers import cash_flow_sync as handler_mod
from monitor_daemon.handlers._throttle_backoff import is_blocked
from monitor_daemon.handlers.cash_flow_sync import CashFlowSyncHandler


def _future_iso(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


@pytest.fixture
def corrupt_state_file(tmp_path: Path) -> Path:
    path = tmp_path / "daemon_state.json"
    path.write_text('{"handlers": {"cash_flow_sync": {"last_run"', encoding="utf-8")
    return path


@pytest.fixture
def valid_state_file(tmp_path: Path) -> Path:
    from utils.atomic_io import atomic_save

    path = tmp_path / "daemon_state.json"
    atomic_save(
        str(path),
        {
            "saved_at": datetime.now().isoformat(),
            "handlers": {
                "cash_flow_sync": {
                    "last_run": None,
                    "enabled": True,
                    "backoff_state": {
                        "throttle_count": 1,
                        "blocked_until": _future_iso(2),
                    },
                }
            },
        },
    )
    return path


def _stub_next_attempt(monkeypatch: pytest.MonkeyPatch, value):
    monkeypatch.setattr(
        handler_mod, "_persisted_next_attempt_at", lambda service=None: value
    )


class TestCorruptStateSeeding:
    def test_active_embargo_survives_a_corrupt_state_file(
        self, corrupt_state_file, monkeypatch
    ):
        embargo = _future_iso(20)
        _stub_next_attempt(monkeypatch, embargo)

        daemon = MonitorDaemon(state_file=corrupt_state_file,
                               respect_market_hours=False)
        handler = CashFlowSyncHandler()
        daemon.register(handler)
        daemon.load_state()

        assert handler._backoff_state["blocked_until"] == embargo
        assert is_blocked(handler._backoff_state, now_utc=datetime.now(timezone.utc))

    def test_an_expired_embargo_is_not_resurrected(self, corrupt_state_file, monkeypatch):
        _stub_next_attempt(monkeypatch, _future_iso(-20))

        daemon = MonitorDaemon(state_file=corrupt_state_file,
                               respect_market_hours=False)
        handler = CashFlowSyncHandler()
        daemon.register(handler)
        daemon.load_state()

        assert handler._backoff_state["blocked_until"] is None

    def test_an_unreadable_service_health_row_does_not_raise(
        self, corrupt_state_file, monkeypatch
    ):
        def boom(service=None):
            raise RuntimeError("stream not found")

        monkeypatch.setattr(handler_mod, "_persisted_next_attempt_at", boom)

        daemon = MonitorDaemon(state_file=corrupt_state_file,
                               respect_market_hours=False)
        handler = CashFlowSyncHandler()
        daemon.register(handler)
        daemon.load_state()  # must not raise

        assert handler._backoff_state["blocked_until"] is None

    def test_a_healthy_state_file_is_not_overwritten_by_the_seed(
        self, valid_state_file, monkeypatch
    ):
        monkeypatch.setattr(
            handler_mod,
            "_persisted_next_attempt_at",
            lambda service=None: pytest.fail("the seed ran on a healthy state file"),
        )

        daemon = MonitorDaemon(state_file=valid_state_file,
                               respect_market_hours=False)
        handler = CashFlowSyncHandler()
        daemon.register(handler)
        daemon.load_state()

        assert handler._backoff_state["throttle_count"] == 1


class TestPersistedNextAttemptRead:
    def test_parses_next_attempt_at_out_of_last_error(self, monkeypatch):
        expected = _future_iso(30)
        rows = [("error", "2026-08-15T21:00:00Z",
                 json.dumps({"message": "Flex throttle (code 1001)",
                             "next_attempt_at": expected}))]
        monkeypatch.setattr(
            handler_mod, "_read_service_health_row", lambda service: rows[0]
        )
        assert handler_mod._persisted_next_attempt_at() == expected

    def test_returns_none_when_there_is_no_row(self, monkeypatch):
        monkeypatch.setattr(handler_mod, "_read_service_health_row", lambda service: None)
        assert handler_mod._persisted_next_attempt_at() is None

    def test_returns_none_when_last_error_is_not_json(self, monkeypatch):
        monkeypatch.setattr(
            handler_mod,
            "_read_service_health_row",
            lambda service: ("error", None, "plain string, not json"),
        )
        assert handler_mod._persisted_next_attempt_at() is None
