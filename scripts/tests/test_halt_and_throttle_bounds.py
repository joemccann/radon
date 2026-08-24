"""REL-068 tranche G — R-165, R-169.

Two guards the 2026-08-22 delta left one layer thinner than the contract
they are documented as enforcing: the kill switch on the modify path, and
the 1018 rate-limit breaker's parse failure.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# R-165 — a modify transmits an order, so the kill switch applies
# --------------------------------------------------------------------------
class TestModifyRespectsTheHalt:
    def test_the_script_imports_the_halt_flag(self):
        src = (REPO / "scripts" / "ib_order_manage.py").read_text()
        assert "trading_halt" in src, (
            "trading_halt.py states the contract as 'every order-placing "
            "process on the host'; the modify path's only kill switch was "
            "the FastAPI route, so the subprocess and the CLI bypassed it"
        )

    def test_a_halt_refuses_a_modify(self, monkeypatch):
        import ib_order_manage as mod
        import trading_halt

        monkeypatch.setattr(trading_halt, "is_trading_halted", lambda: True)
        monkeypatch.setattr(
            trading_halt, "get_halt_state", lambda: {"reason": "drill"}
        )
        client = MagicMock()
        with pytest.raises(SystemExit) as exc:
            mod.modify_order(client, 1, 2, 22.5, "127.0.0.1", 4001)
        assert exc.value.code != 0
        client.ib.reqAllOpenOrders.assert_not_called()

    def test_the_refusal_names_the_halt_and_the_resume_path(self, monkeypatch, capsys):
        import ib_order_manage as mod
        import trading_halt

        monkeypatch.setattr(trading_halt, "is_trading_halted", lambda: True)
        monkeypatch.setattr(
            trading_halt, "get_halt_state", lambda: {"reason": "drill"}
        )
        with pytest.raises(SystemExit):
            mod.modify_order(MagicMock(), 1, 2, 22.5, "127.0.0.1", 4001)
        out = capsys.readouterr().out
        assert "HALTED" in out and "drill" in out
        assert "/trading/resume" in out

    def test_cancel_is_still_allowed_under_a_halt(self, monkeypatch):
        """Cancelling reduces risk, and cancel-all is the kill switch's own
        recovery path — halting it would trap working orders."""
        import ib_order_manage as mod
        import trading_halt

        monkeypatch.setattr(trading_halt, "is_trading_halted", lambda: True)
        monkeypatch.setattr(mod, "find_trade", lambda *a, **k: None)
        with pytest.raises(SystemExit):
            mod.cancel_order(MagicMock(), 1, 2, "127.0.0.1", 4001)
        # It reached find_trade (the "order missing" exit), not a halt refusal.

    def test_an_unreadable_flag_still_refuses(self, monkeypatch):
        import ib_order_manage as mod
        import trading_halt

        monkeypatch.setattr(
            trading_halt,
            "is_trading_halted",
            lambda: True,  # the module already fails closed on unreadable
        )
        monkeypatch.setattr(trading_halt, "get_halt_state", lambda: {})
        with pytest.raises(SystemExit):
            mod.modify_order(MagicMock(), 1, 2, 22.5, "127.0.0.1", 4001)


# --------------------------------------------------------------------------
# R-169 — a corrupt deadline must not disarm the rate-limit breaker
# --------------------------------------------------------------------------
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


class TestThrottleBreakerFailsClosed:
    def test_a_malformed_deadline_blocks(self):
        from monitor_daemon.handlers._throttle_backoff import is_blocked

        assert is_blocked({"blocked_until": "not-a-timestamp"}, now_utc=NOW) is True, (
            "a corrupt persisted string silently disarmed the 1018 breaker; "
            "unlike 1025, nothing else stands the caller down"
        )

    def test_a_non_string_deadline_blocks(self):
        from monitor_daemon.handlers._throttle_backoff import is_blocked

        assert is_blocked({"blocked_until": 12345}, now_utc=NOW) is True

    def test_no_deadline_at_all_is_still_allowed(self):
        from monitor_daemon.handlers._throttle_backoff import is_blocked

        assert is_blocked({}, now_utc=NOW) is False
        assert is_blocked({"blocked_until": None}, now_utc=NOW) is False

    def test_a_live_deadline_still_blocks_and_a_past_one_does_not(self):
        from monitor_daemon.handlers._throttle_backoff import is_blocked

        future = (NOW + timedelta(hours=1)).isoformat()
        past = (NOW - timedelta(hours=1)).isoformat()
        assert is_blocked({"blocked_until": future}, now_utc=NOW) is True
        assert is_blocked({"blocked_until": past}, now_utc=NOW) is False

    def test_a_malformed_deadline_is_logged(self, caplog):
        from monitor_daemon.handlers._throttle_backoff import is_blocked

        with caplog.at_level("WARNING"):
            is_blocked({"blocked_until": "garbage"}, now_utc=NOW)
        assert any("blocked_until" in r.message for r in caplog.records)

    def test_blocked_until_reports_the_corruption_rather_than_none(self):
        from monitor_daemon.handlers._throttle_backoff import (
            blocked_until,
            CORRUPT_DEADLINE_SENTINEL,
        )

        assert blocked_until({"blocked_until": "garbage"}) is CORRUPT_DEADLINE_SENTINEL
        assert blocked_until({}) is None
