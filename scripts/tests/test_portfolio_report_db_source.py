"""Portfolio report reads open executed trades from Turso journal rows."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_load_trade_log_reads_journal_rows() -> None:
    from portfolio_report import load_trade_log

    rows = [
        {"ticker": "AAPL", "decision": "EXECUTED"},
        {"ticker": "MSFT", "decision": "EXECUTED", "close_date": "2026-06-01"},
        {"ticker": "NVDA", "decision": "WATCH"},
    ]
    with patch("db.readers.read_journal_trades", return_value=rows) as reader:
        result = load_trade_log()

    reader.assert_called_once_with()
    assert result == {"AAPL": rows[0]}
