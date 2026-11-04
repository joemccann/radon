"""Unit tests for fill_monitor open/close action labels + structure fields."""

from __future__ import annotations

from types import SimpleNamespace

from monitor_daemon.handlers.fill_monitor import FillMonitorHandler


# Action labels: scripts/tests/test_journal_action.py (shared classifier).


def test_structure_label_closed_put_for_buy_to_close():
    label = FillMonitorHandler._structure_label(
        "BUY_TO_CLOSE", "OPT", 880, "P", "20260717"
    )
    assert label.startswith("Closed")
    assert "Put" in label
    assert "880" in label
