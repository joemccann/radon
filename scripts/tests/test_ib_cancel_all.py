#!/usr/bin/env python3
"""REL-004 — ib_cancel_all: master-client global cancel with drain
verification. Never reports success while orders remain working."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import ib_cancel_all


def _client_with_open_trades(snapshots):
    """Fake IBClient whose get_open_orders() returns successive snapshots
    (list per call; last snapshot repeats)."""
    client = MagicMock()
    state = {"i": 0}

    def get_open_orders():
        idx = min(state["i"], len(snapshots) - 1)
        state["i"] += 1
        return snapshots[idx]

    client.get_open_orders.side_effect = get_open_orders
    return client


def _trade(order_id):
    t = MagicMock()
    t.order.orderId = order_id
    return t


class TestCancelAll:
    def test_cancels_and_drains(self):
        client = _client_with_open_trades([
            [_trade(1), _trade(2), _trade(3)],  # before
            [],                                  # after cancel
        ])
        with patch.object(ib_cancel_all, "IBClient", return_value=client):
            result = ib_cancel_all.cancel_all(drain_timeout=1.0, poll_step=0.0)
        assert result["status"] == "ok"
        assert result["open_before"] == 3
        assert result["remaining"] == 0
        client.global_cancel.assert_called_once()

    def test_reports_error_when_orders_remain(self):
        remaining = [_trade(7)]
        client = _client_with_open_trades([
            [_trade(7), _trade(8)],
            remaining,
        ])
        with patch.object(ib_cancel_all, "IBClient", return_value=client):
            result = ib_cancel_all.cancel_all(drain_timeout=0.2, poll_step=0.0)
        assert result["status"] == "error"
        assert result["open_before"] == 2
        assert result["remaining"] == 1
        assert 7 in result["remaining_order_ids"]

    def test_no_open_orders_is_ok_noop(self):
        client = _client_with_open_trades([[]])
        with patch.object(ib_cancel_all, "IBClient", return_value=client):
            result = ib_cancel_all.cancel_all(drain_timeout=0.2, poll_step=0.0)
        assert result["status"] == "ok"
        assert result["open_before"] == 0
        client.global_cancel.assert_not_called()
