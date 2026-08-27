#!/usr/bin/env python3
"""``ib_execute`` is a live order placer and must sit behind the fat-finger caps.

R-250 (standing sweep 6). ``ib_execute.py`` honours the kill switch — the
REL-004 fix is correctly applied — but contained **zero** references to
``check_order_limits``, so none of ``max_qty``, ``max_notional`` or the combo
bounds applied to it. Every other placer is wired (``ib_place_order``,
``api/server.py`` place and modify).

The path is not hypothetical: ``risk_reversal.py`` renders a copy-paste-ready
``python3 scripts/ib_execute.py … --qty {combo["max_qty"]} … --yes`` command
into the operator's own HTML report, and ``--yes`` also skips the
``CONFIRM ORDER?`` prompt. The documented workflow therefore handed the
operator a command line that bypassed both the confirmation and the
server-side caps, with a quantity computed by the report generator.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import ib_execute  # noqa: E402
import order_limits  # noqa: E402
import trading_halt  # noqa: E402


@pytest.fixture
def not_halted(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "trading_halt.json")
    return tmp_path


def _executor(placed: list):
    executor = MagicMock(name="OrderExecutor")
    executor.connect.return_value = True
    executor.get_stock_contract.return_value = MagicMock(localSymbol="AAPL")
    executor.get_option_contract.return_value = MagicMock(localSymbol="AAPL 260116C00200000")
    executor.get_market_data.return_value = {"bid": 1.0, "ask": 1.1, "mid": 1.05, "spread": 0.1}

    def place_order(*args, **kwargs):
        placed.append((args, kwargs))
        return None

    executor.place_order.side_effect = place_order
    return executor


def _run(argv: list[str], placed: list) -> int:
    with patch.object(ib_execute, "OrderExecutor", return_value=_executor(placed)):
        with patch.object(sys, "argv", ["ib_execute.py", *argv]):
            try:
                ib_execute.main()
            except SystemExit as exc:  # argparse / explicit exits
                return int(exc.code or 0)
    return 0


class TestOrderLimitsAreEnforced:
    def test_over_cap_quantity_is_refused_before_placement(
        self, not_halted, monkeypatch, capsys
    ):
        monkeypatch.setenv("RADON_MAX_STOCK_ORDER_QTY", "10")
        placed: list = []
        code = _run(
            ["--type", "stock", "--symbol", "AAPL", "--qty", "5000",
             "--side", "BUY", "--limit", "1.00", "--yes", "--no-log"],
            placed,
        )
        assert placed == [], "an over-cap order reached the broker"
        assert code != 0
        assert "limit" in capsys.readouterr().out.lower()

    def test_over_cap_notional_is_refused(self, not_halted, monkeypatch, capsys):
        monkeypatch.setenv("RADON_MAX_ORDER_NOTIONAL", "100")
        placed: list = []
        code = _run(
            ["--type", "stock", "--symbol", "AAPL", "--qty", "10",
             "--side", "BUY", "--limit", "500.00", "--yes", "--no-log"],
            placed,
        )
        assert placed == [], "an over-notional order reached the broker"
        assert code != 0

    def test_the_yes_flag_does_not_bypass_the_caps(self, not_halted, monkeypatch):
        """--yes skips the CONFIRM prompt; it must not skip the caps too."""
        monkeypatch.setenv("RADON_MAX_STOCK_ORDER_QTY", "10")
        placed: list = []
        _run(
            ["--type", "stock", "--symbol", "AAPL", "--qty", "999",
             "--side", "SELL", "--limit", "1.00", "--yes", "--no-log"],
            placed,
        )
        assert placed == []

    def test_a_within_cap_order_still_places(self, not_halted, monkeypatch):
        monkeypatch.setenv("RADON_MAX_STOCK_ORDER_QTY", "1000")
        monkeypatch.setenv("RADON_MAX_ORDER_NOTIONAL", "1000000")
        placed: list = []
        _run(
            ["--type", "stock", "--symbol", "AAPL", "--qty", "5",
             "--side", "BUY", "--limit", "1.00", "--yes", "--no-log"],
            placed,
        )
        assert placed, "the caps must not refuse an order inside them"

    def test_dry_run_still_exits_before_the_caps_matter(self, not_halted):
        placed: list = []
        code = _run(
            ["--type", "stock", "--symbol", "AAPL", "--qty", "5",
             "--side", "BUY", "--limit", "1.00", "--dry-run"],
            placed,
        )
        assert placed == []
        assert code == 0


class TestReportRenderedCommand:
    def test_the_risk_reversal_report_does_not_render_a_yes_invocation(self):
        """A report the operator reads must not hand them a --yes command line.

        --yes skips the CONFIRM ORDER? prompt, so a copy-paste from the report
        places a live order in one step with a quantity the report generator
        computed.
        """
        source = (Path(__file__).parent.parent / "risk_reversal.py").read_text(encoding="utf-8")
        rendered = [
            line for line in source.splitlines()
            if "ib_execute.py" in line and "--yes" in line and not line.lstrip().startswith("#")
        ]
        assert not rendered, f"report renders a --yes order command: {rendered}"
