"""flex_token_check hosts the daily service_health_events prune (DUR-11).

The handler is the existing daily (interval 86400s, requires_market_hours
False) monitor-daemon slot, so the retention sweep piggybacks on it rather
than adding new daemon wiring. Contract under test:

  - success path includes ``events_pruned`` in the result
  - a prune failure raises (BaseHandler no-latch -> retried next cycle)
  - hosts without the prune symbol (older db.writer) skip gracefully
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from monitor_daemon.handlers.base import BaseHandler  # noqa: E402
from monitor_daemon.handlers.flex_token_check import FlexTokenCheck  # noqa: E402


@pytest.fixture
def handler(tmp_path, monkeypatch: pytest.MonkeyPatch):
    h = FlexTokenCheck()
    # Point the handler at a missing config so _execute_inner takes the
    # cheap skip path — these tests exercise the prune wiring, not Flex.
    monkeypatch.setattr(
        "monitor_daemon.handlers.flex_token_check.CONFIG_PATH",
        tmp_path / "flex_token_config.json",
    )
    return h


@pytest.fixture
def writer(monkeypatch: pytest.MonkeyPatch):
    import db.writer as writer_mod
    monkeypatch.setattr(writer_mod, "record_service_health", MagicMock(), raising=False)
    monkeypatch.setattr(writer_mod, "_now_iso", MagicMock(return_value="2026-06-12T00:00:00Z"), raising=False)
    return writer_mod


class TestPruneWiring:
    def test_success_path_reports_pruned_count(self, handler, writer, monkeypatch):
        monkeypatch.setattr(
            writer, "prune_service_health_events", MagicMock(return_value=42), raising=False,
        )
        result = handler.execute()
        assert result["events_pruned"] == 42

    def test_prune_failure_raises_so_daily_slot_is_not_burned(self, handler, writer, monkeypatch):
        monkeypatch.setattr(
            writer,
            "prune_service_health_events",
            MagicMock(side_effect=ValueError("Hrana: dns error")),
            raising=False,
        )
        with pytest.raises(ValueError, match="dns error"):
            handler.execute()
        # BaseHandler.run must not latch last_run on the raise.
        outcome = handler.run()
        assert outcome["status"] == "error"
        assert handler.last_run is None

    def test_missing_prune_symbol_skips_gracefully(self, handler, writer, monkeypatch):
        monkeypatch.delattr(writer, "prune_service_health_events", raising=False)
        result = handler.execute()
        assert result["events_pruned"] is None

    def test_handler_remains_daily_and_off_hours(self, handler):
        assert handler.interval_seconds == 86400
        assert handler.requires_market_hours is False
        assert isinstance(handler, BaseHandler)
