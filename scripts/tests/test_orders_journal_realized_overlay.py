"""``/orders`` replaces IB's drifting commission-report realizedPNL with the
journal average-cost figure, and keeps IB's when the journal is unreadable."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from api import server as srv

_OPEN = json.dumps({
    "ticker": "SLV", "strike": 60.0, "right": "C", "expiry": "20261016",
    "action": "BUY_OPTION", "contracts": 10, "fill_price": 1.0, "commission": 0.0,
    "total_cost": 1000.0, "ib_exec_id": "open-1", "date": "2026-08-07",
})
_CLOSE = json.dumps({
    "ticker": "SLV", "strike": 60.0, "right": "C", "expiry": "20261016",
    "action": "SELL_OPTION", "contracts": 10, "fill_price": 3.0, "commission": 0.0,
    "total_cost": 3000.0, "ib_exec_id": "close-1", "date": "2026-08-24",
})


def _closing_fill():
    return {
        "execId": "close-1",
        "symbol": "SLV C60",
        "contract": {"symbol": "SLV", "secType": "OPT", "strike": 60.0, "right": "C",
                     "expiry": "2026-10-16"},
        "side": "SLD",
        "quantity": 10.0,
        "realizedPNL": 1234.5,
    }


@pytest.mark.asyncio
async def test_journal_figure_replaces_ib_realized_pnl():
    def fake_execute(sql, args=(), timeout=None):
        assert "FROM journal" in sql
        return [(_OPEN, "2026-08-07", "w1"), (_CLOSE, "2026-08-24", "w2")]

    fills = [_closing_fill()]
    with patch.object(srv.db_http, "hrana_execute", side_effect=fake_execute):
        await srv._overlay_journal_realized_pnl(fills)

    assert fills[0]["realizedPNL"] == 2000.0
    assert fills[0]["ibRealizedPNL"] == 1234.5
    assert fills[0]["realizedPNLSource"] == "journal"


@pytest.mark.asyncio
async def test_journal_read_failure_keeps_ib_realized_pnl():
    def failing_execute(sql, args=(), timeout=None):
        raise srv.db_http.DbHttpError("upstream forward failed")

    fills = [_closing_fill()]
    with patch.object(srv.db_http, "hrana_execute", side_effect=failing_execute):
        await srv._overlay_journal_realized_pnl(fills)

    assert fills[0]["realizedPNL"] == 1234.5
    assert "ibRealizedPNL" not in fills[0]
