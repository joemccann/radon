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
    # A valid, far-from-expiry config so _execute_inner takes its ordinary
    # path — these tests exercise the prune wiring, not Flex. R-131 made a
    # MISSING config a soft failure (it means the token TTL is unmonitored),
    # which is covered by test_silent_degradation_bounds.py.
    from datetime import datetime, timedelta, timezone

    cfg = tmp_path / "flex_token_config.json"
    expires = (datetime.now(timezone.utc) + timedelta(days=300)).isoformat()
    cfg.write_text('{"expires_at": "' + expires + '", "reminder_days": [30]}\n')
    monkeypatch.setattr(
        "monitor_daemon.handlers.flex_token_check.CONFIG_PATH", cfg,
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

    def test_portfolio_snapshots_are_not_pruned_here(self, handler, writer, monkeypatch):
        # Deletion is owned by the archive pipeline (it exports off-box first).
        # The daily flex-token slot must NOT delete portfolio_snapshots.
        monkeypatch.setattr(
            writer, "prune_service_health_events", MagicMock(return_value=0), raising=False,
        )
        boom = MagicMock(side_effect=AssertionError("prune_portfolio_snapshots must not be called"))
        monkeypatch.setattr(writer, "prune_portfolio_snapshots", boom, raising=False)
        result = handler.execute()
        boom.assert_not_called()
        assert "portfolio_snapshots_pruned" not in result

    def test_handler_remains_daily_and_off_hours(self, handler):
        assert handler.interval_seconds == 86400
        assert handler.requires_market_hours is False
        assert isinstance(handler, BaseHandler)
