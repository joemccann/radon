"""Unit tests for fill_monitor open/close action labels + structure fields."""

from __future__ import annotations

from types import SimpleNamespace

from monitor_daemon.handlers.fill_monitor import FillMonitorHandler


def test_side_to_action_cover_short_is_buy_to_close():
    assert FillMonitorHandler._side_to_action("BUY", "OPT", prior_qty=-10) == "BUY_TO_CLOSE"


def test_side_to_action_open_long_is_buy_option():
    assert FillMonitorHandler._side_to_action("BUY", "OPT", prior_qty=0) == "BUY_OPTION"


def test_side_to_action_close_long_is_sell_option():
    assert FillMonitorHandler._side_to_action("SELL", "OPT", prior_qty=5) == "SELL_OPTION"


def test_side_to_action_open_short_is_sell_to_open():
    assert FillMonitorHandler._side_to_action("SELL", "OPT", prior_qty=0) == "SELL_TO_OPEN"


def test_structure_label_closed_put_for_buy_to_close():
    label = FillMonitorHandler._structure_label(
        "BUY_TO_CLOSE", "OPT", 880, "P", "20260717"
    )
    assert label.startswith("Closed")
    assert "Put" in label
    assert "880" in label
