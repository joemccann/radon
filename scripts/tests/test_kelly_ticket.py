"""D6 ruin / path-to-zero hooks: kelly_ticket, portfolio_capacity, drawdown."""
from __future__ import annotations

import pytest

from scripts.kelly import kelly_ticket, portfolio_capacity


class TestD6RuinHooks:
    def test_d6_undefined_risk_zero_max_loss(self):
        ticket = kelly_ticket(
            prob_win=0.6, max_gain=100, max_loss=0, bankroll=100_000
        )
        assert ticket["reason"] == "UNDEFINED_RISK"
        assert ticket["contracts"] == 0

    def test_d6_undefined_risk_negative_max_loss(self):
        ticket = kelly_ticket(
            prob_win=0.6, max_gain=100, max_loss=-1, bankroll=100_000
        )
        assert ticket["reason"] == "UNDEFINED_RISK"
        assert ticket["contracts"] == 0

    def test_d6_cap_below_one_contract(self):
        ticket = kelly_ticket(
            prob_win=0.6, max_gain=3000, max_loss=3000, bankroll=100_000
        )
        assert ticket["contracts"] == 0
        assert ticket["reason"] == "CAP_BELOW_ONE_CONTRACT"

    def test_d6_no_edge_ticket(self):
        ticket = kelly_ticket(
            prob_win=0.3, max_gain=100, max_loss=100, bankroll=100_000
        )
        assert ticket["contracts"] == 0
        assert ticket["reason"] == "NO_EDGE"

    def test_d6_capacity_ok_on_exact_20_pct(self):
        cap = portfolio_capacity(100_000, [9_000] * 2, 2_000)
        assert cap["ok"] is True

    def test_d6_capacity_refuses_over_20_pct(self):
        cap = portfolio_capacity(100_000, [9_000] * 2, 2_001)
        assert cap["ok"] is False
        assert cap["reason"] == "CAPACITY"

    def test_d6_drawdown_halt_at_15_pct(self):
        ticket = kelly_ticket(
            prob_win=0.6,
            max_gain=300,
            max_loss=100,
            bankroll=100_000,
            nav_peak=100_000,
            nav_now=85_000,
        )
        assert ticket["reason"] == "DRAWDOWN_HALT"
        assert ticket["contracts"] == 0

    def test_d6_drawdown_just_inside_sizes(self):
        ticket = kelly_ticket(
            prob_win=0.4,
            max_gain=300,
            max_loss=100,
            bankroll=100_000,
            nav_peak=100_000,
            nav_now=85_001,
        )
        assert ticket["reason"] is None
        assert ticket["contracts"] >= 1

    def test_d6_sequence_capacity_refuses_ninth_capped_ticket(self):
        bankroll = 100_000
        open_losses = []
        last = None
        for _ in range(40):
            last = kelly_ticket(
                prob_win=0.4,
                max_gain=300,
                max_loss=100,
                bankroll=bankroll,
                open_max_losses=open_losses,
            )
            if last["reason"] == "CAPACITY":
                break
            assert last["contracts"] >= 1
            open_losses.append(last["max_loss_total"])
        assert last is not None
        assert last["reason"] == "CAPACITY"
        assert last["contracts"] == 0
        assert len(open_losses) == 8
