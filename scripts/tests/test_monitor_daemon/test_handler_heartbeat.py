#!/usr/bin/env python3
"""
Heartbeat-discipline tests for monitor_daemon handlers — Red/Green TDD.

The banner is structurally blind to handler outcomes that don't write to
``service_health``. These tests pin every handler's success+failure path
to a real ``record_service_health(...)`` call so a stale ``ok`` or a
silenced error can never re-emerge.

DUR-14: the heartbeat moved from per-handler execute() wrappers into
``BaseHandler.run()`` (structural — see
test_base_handler_structural_heartbeat.py), so these tests now drive
``run()``, the daemon's actual entry point. cash_flow_sync keeps a custom
execute() wrapper for throttle metadata and is still pinned via execute().

Service-name kebab-case mapping is asserted alongside state semantics
because the staleness gate keys off the kebab name.

``db.writer`` depends on ``libsql_experimental`` which isn't installed in
the test environment, so we inject a fake ``db.writer`` module into
``sys.modules`` before the handler's lazy import resolves.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# --------------------------------------------------------------------- helpers


def _install_fake_db_writer() -> MagicMock:
    """Stub ``db.writer.record_service_health`` for hermetic tests."""
    record_mock = MagicMock(name="record_service_health")
    now_iso_mock = MagicMock(name="_now_iso", return_value="2026-05-09T09:00:00Z")

    # If a real db.writer module is already loaded, patch its symbol; otherwise
    # synthesize the package + module so lazy imports inside handlers resolve.
    real_writer = sys.modules.get("db.writer")
    if real_writer is not None:
        real_writer.record_service_health = record_mock  # type: ignore[attr-defined]
        real_writer._now_iso = now_iso_mock  # type: ignore[attr-defined]
        return record_mock

    fake_writer = types.ModuleType("db.writer")
    fake_writer.record_service_health = record_mock  # type: ignore[attr-defined]
    fake_writer._now_iso = now_iso_mock  # type: ignore[attr-defined]
    # Mirror the rest of the symbols the handlers import lazily.
    fake_writer.upsert_journal_entry = MagicMock(name="upsert_journal_entry")  # type: ignore[attr-defined]
    fake_writer.upsert_app_config = MagicMock(name="upsert_app_config")  # type: ignore[attr-defined]

    fake_db_pkg = sys.modules.get("db") or types.ModuleType("db")
    fake_db_pkg.writer = fake_writer  # type: ignore[attr-defined]

    sys.modules["db"] = fake_db_pkg
    sys.modules["db.writer"] = fake_writer
    return record_mock


@pytest.fixture(autouse=True)
def fake_db_writer():
    """Reset the stub between tests so call-history doesn't bleed across."""
    yield _install_fake_db_writer()


def _called_with_state(spy: MagicMock, expected_service: str, expected_state: str) -> bool:
    for call in spy.call_args_list:
        args, kwargs = call
        service = args[0] if args else kwargs.get("service")
        state = args[1] if len(args) >= 2 else kwargs.get("state")
        if service == expected_service and state == expected_state:
            return True
    return False


# --------------------------------------------------------------------- fill_monitor

class TestFillMonitorHeartbeat:
    """Service name: ``fill-monitor``."""

    def test_records_ok_on_successful_run(self, fake_db_writer):
        from monitor_daemon.handlers.fill_monitor import FillMonitorHandler

        with patch("monitor_daemon.handlers.fill_monitor.IBClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.get_open_orders.return_value = []
            mock_cls.return_value = mock_client

            handler = FillMonitorHandler(send_notifications=False)
            handler.run()

        assert _called_with_state(fake_db_writer, "fill-monitor", "ok"), (
            f"Expected fill-monitor 'ok' heartbeat, got {fake_db_writer.call_args_list!r}"
        )

    def test_records_error_when_ib_fetch_fails(self, fake_db_writer):
        from monitor_daemon.handlers.fill_monitor import FillMonitorHandler

        with patch("monitor_daemon.handlers.fill_monitor.IBClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.connect.side_effect = ConnectionError("Gateway down")
            mock_cls.return_value = mock_client

            handler = FillMonitorHandler(send_notifications=False)
            handler.run()

        assert _called_with_state(fake_db_writer, "fill-monitor", "error"), (
            f"Expected fill-monitor 'error' heartbeat, got {fake_db_writer.call_args_list!r}"
        )


# --------------------------------------------------------------------- exit_orders

class TestExitOrdersHeartbeat:
    """Service name: ``exit-orders``."""

    def test_records_ok_on_successful_run(self, tmp_path, fake_db_writer):
        import json as _json
        from monitor_daemon.handlers.exit_orders import ExitOrdersHandler

        trade_log = tmp_path / "trade_log.json"
        trade_log.write_text(_json.dumps({"trades": []}))

        with patch("monitor_daemon.handlers.exit_orders.IBClient") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client

            handler = ExitOrdersHandler(trade_log_path=trade_log)
            handler.run()

        assert _called_with_state(fake_db_writer, "exit-orders", "ok")

    def test_records_error_when_ib_fetch_fails(self, tmp_path, fake_db_writer):
        import json as _json
        from monitor_daemon.handlers.exit_orders import ExitOrdersHandler

        trade_log = tmp_path / "trade_log.json"
        trade_log.write_text(_json.dumps({
            "trades": [{
                "id": 1,
                "ticker": "GOOG",
                "exit_orders": {
                    "target": {
                        "price": 15.00,
                        "status": "PENDING",
                        "order_id": None,
                        "contracts": 1,
                        "contract_spec": {
                            "symbol": "GOOG", "expiry": "20260417",
                            "strike": 315, "right": "C",
                        },
                    },
                },
            }],
        }))

        with patch("monitor_daemon.handlers.exit_orders.IBClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.connect.side_effect = ConnectionError("Gateway down")
            mock_cls.return_value = mock_client

            handler = ExitOrdersHandler(trade_log_path=trade_log)
            handler.run()

        assert _called_with_state(fake_db_writer, "exit-orders", "error")


# --------------------------------------------------------------------- journal_sync

class TestJournalSyncHeartbeat:
    """Service name: ``journal-sync``."""

    def test_records_ok_on_successful_run(self, tmp_path, fake_db_writer):
        from monitor_daemon.handlers.journal_sync import JournalSyncHandler
        from utils.atomic_io import atomic_save

        trade_log = tmp_path / "trade_log.json"
        atomic_save(str(trade_log), {"trades": []})

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.get_fills.return_value = []
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log)
            handler.run()

        assert _called_with_state(fake_db_writer, "journal-sync", "ok")

    def test_records_error_when_ib_fetch_fails(self, tmp_path, fake_db_writer):
        from monitor_daemon.handlers.journal_sync import JournalSyncHandler
        from utils.atomic_io import atomic_save

        trade_log = tmp_path / "trade_log.json"
        atomic_save(str(trade_log), {"trades": []})

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.connect.side_effect = ConnectionError("Gateway down")
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log)
            handler.run()

        assert _called_with_state(fake_db_writer, "journal-sync", "error")


# --------------------------------------------------------------------- cash_flow_sync

class TestCashFlowSyncHeartbeat:
    """Service name: ``cash-flow-sync``."""

    def test_records_ok_on_successful_run(self, monkeypatch, fake_db_writer):
        from monitor_daemon.handlers.cash_flow_sync import CashFlowSyncHandler

        monkeypatch.setenv("IB_FLEX_TOKEN", "token")
        monkeypatch.setenv("IB_FLEX_NAV_QUERY_ID", "1497709")

        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = "Synced 0 cash flows. Breakdown: {}\n"
        completed.stderr = ""

        with patch("monitor_daemon.handlers.cash_flow_sync.subprocess.run", return_value=completed), \
             patch("monitor_daemon.handlers.cash_flow_sync.Path.exists", return_value=True):
            handler = CashFlowSyncHandler()
            handler.execute()

        assert _called_with_state(fake_db_writer, "cash-flow-sync", "ok")

    def test_records_error_when_subprocess_fails(self, monkeypatch, fake_db_writer):
        """execute() now raises on inner_error so BaseHandler.run()
        doesn't latch last_run — that lets the handler retry within the
        same day instead of burning 24h on a single transient Flex
        timeout (2026-05-14 incident). The error heartbeat still fires
        before the raise.
        """
        from monitor_daemon.handlers.cash_flow_sync import CashFlowSyncHandler

        monkeypatch.setenv("IB_FLEX_TOKEN", "token")
        monkeypatch.setenv("IB_FLEX_NAV_QUERY_ID", "1497709")

        completed = MagicMock()
        completed.returncode = 1
        completed.stdout = ""
        completed.stderr = "boom\n"

        with patch("monitor_daemon.handlers.cash_flow_sync.subprocess.run", return_value=completed), \
             patch("monitor_daemon.handlers.cash_flow_sync.Path.exists", return_value=True):
            handler = CashFlowSyncHandler()
            with pytest.raises(RuntimeError):
                handler.execute()

        assert _called_with_state(fake_db_writer, "cash-flow-sync", "error")


# --------------------------------------------------------------------- flex_token_check

class TestFlexTokenCheckHeartbeat:
    """Service name: ``flex-token-check``."""

    def test_records_ok_on_successful_run(self, tmp_path, monkeypatch, fake_db_writer):
        import json as _json
        from datetime import datetime, timedelta, timezone
        from monitor_daemon.handlers import flex_token_check as mod

        cfg_path = tmp_path / "flex_token_config.json"
        cfg_path.write_text(_json.dumps({
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=120)).isoformat(),
            "reminder_days": [30, 14, 7, 1],
            "reminders_sent": {},
            "renewal_url": "https://example.com",
            "breadcrumb": "test",
        }))
        monkeypatch.setattr(mod, "CONFIG_PATH", cfg_path)

        # Block the dual-write side path so the test stays hermetic.
        with patch.object(mod, "_dual_write_flex_state_to_app_config", return_value=None):
            handler = mod.FlexTokenCheck()
            handler.run()

        assert _called_with_state(fake_db_writer, "flex-token-check", "ok")

    def test_records_error_when_config_unreadable(self, tmp_path, monkeypatch, fake_db_writer):
        from monitor_daemon.handlers import flex_token_check as mod

        cfg_path = tmp_path / "flex_token_config.json"
        cfg_path.write_text("{ not json")  # invalid JSON triggers ValueError
        monkeypatch.setattr(mod, "CONFIG_PATH", cfg_path)

        with patch.object(mod, "_dual_write_flex_state_to_app_config", return_value=None):
            handler = mod.FlexTokenCheck()
            result = handler.run()  # run() converts the raise into status=error
            assert result["status"] == "error"
            assert handler.last_run is None  # raise-don't-latch preserved

        assert _called_with_state(fake_db_writer, "flex-token-check", "error")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
