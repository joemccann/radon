"""R-347 / R-348 / R-373 / R-374 / REL-127: the session-fill basis is
arithmetically right and cannot disagree with the entry date.

R-347: `build_fill_basis` prefers `Execution.avgPrice` over the per-execution
`price` while weighting it by that single execution's `shares`. IB reports
`avgPrice` as the running average over the ORDER's cumulative quantity, so one
order for 25 filling 10 @ $5.00 then 15 @ $6.00 yields lots (10, 5.00) and
(15, 5.60); the vwap comes out $5.36 against a true $5.60 and `entry_cost`
$13,400 against a real $14,000 — stamped `basis_source = 'session_fills'` so
it outranks IB's own avgCost AND the journal.

R-348: `_session_fill_cover` and `_session_fill_open_date` use DIFFERENT
coverage tests, so a same-session round trip takes today's basis but keeps a
stale entry_date. Total P&L is then measured off today's basis while Today
P&L uses yesterday's close.

R-373: `entry_cost = need * vwap * 100.0` hardcodes the multiplier that
`fetch_positions` reads correctly elsewhere; `build_fill_basis` keys only on
symbol|expiry|right|strike and discards it.

R-374: the override is applied PER LEG with no all-legs gate, while the
position-level `basis_source` and `session_fill_date` both require every leg.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ib_sync  # noqa: E402


def _fill(*, side, shares, price, avg_price, time, multiplier="100",
          symbol="META", strike=575.0, expiry="20260828", right="C"):
    contract = SimpleNamespace(
        symbol=symbol, secType="OPT", strike=strike, right=right,
        lastTradeDateOrContractMonth=expiry, multiplier=multiplier,
    )
    execution = SimpleNamespace(
        side=side, shares=shares, avgPrice=avg_price, price=price,
        time=datetime.fromisoformat(time),
    )
    return SimpleNamespace(contract=contract, execution=execution)


class TestPerLotPriceIsTheExecutionPrice:
    """R-347: avgPrice is order-cumulative; the lot needs its OWN price."""

    def test_a_two_fill_order_yields_the_true_vwap(self):
        # One order for 25: 10 @ $5.00, then 15 @ $6.00.
        # IB reports avgPrice as the RUNNING average: 5.00, then 5.60.
        fills = [
            _fill(side="BOT", shares=10, price=5.00, avg_price=5.00,
                  time="2026-08-27T14:49:55"),
            _fill(side="BOT", shares=15, price=6.00, avg_price=5.60,
                  time="2026-08-27T14:50:10"),
        ]
        basis = ib_sync.build_fill_basis(fills)
        key = next(iter(basis))
        prices = [lot["per_share"] for lot in basis[key]["lots"]]
        assert prices == [5.00, 6.00], (
            f"lots must carry their own execution price, got {prices}"
        )

        cover = ib_sync._session_fill_cover(basis[key], 25)
        assert cover is not None
        entry_cost, avg_cost = cover
        assert entry_cost == pytest.approx(14000.0), (
            "the running average weighted by the single execution's shares "
            f"prices the position at $13,400 against a real $14,000; got {entry_cost}"
        )
        assert avg_cost == pytest.approx(560.0)

    def test_an_absent_price_still_falls_back_to_avg_price(self):
        fills = [
            _fill(side="BOT", shares=10, price=None, avg_price=5.00,
                  time="2026-08-27T14:49:55"),
        ]
        basis = ib_sync.build_fill_basis(fills)
        key = next(iter(basis))
        assert basis[key]["lots"][0]["per_share"] == 5.00

    def test_a_single_fill_order_is_unchanged(self):
        fills = [
            _fill(side="BOT", shares=25, price=5.55, avg_price=5.55,
                  time="2026-08-27T14:49:55"),
        ]
        basis = ib_sync.build_fill_basis(fills)
        key = next(iter(basis))
        cover = ib_sync._session_fill_cover(basis[key], 25)
        assert cover[0] == pytest.approx(13875.0)


class TestCoverAndOpenDateAgree:
    """R-348: one coverage decision, not two."""

    def _round_trip(self):
        # Overnight 25 long, sell 25, rebuy 25 — all in one session.
        return [
            _fill(side="SLD", shares=25, price=5.00, avg_price=5.00,
                  time="2026-08-27T14:00:00"),
            _fill(side="BOT", shares=25, price=6.00, avg_price=6.00,
                  time="2026-08-27T15:00:00"),
        ]

    def test_a_same_session_round_trip_agrees_on_both(self):
        """The FIFO walk resolves this in the direction that is actually true.

        Selling the overnight 25 and rebuying 25 means the 25 held now really
        ARE today's fills, so both the basis and the date should be today's.
        The old pair disagreed: the cover matched the newest same-sign lots
        and took today's price while the open date saw net 0 != 25 and fell
        back to the journal's earliest date.
        """
        basis = ib_sync.build_fill_basis(self._round_trip())
        key = next(iter(basis))
        cover = ib_sync._session_fill_cover(basis[key], 25)
        open_date = ib_sync._session_fill_open_date(basis[key], 25)

        assert (cover is None) == (open_date is None), (
            f"cover={cover} open_date={open_date} must agree"
        )
        assert cover is not None
        assert cover[0] == pytest.approx(15000.0), "25 x $6.00 x 100, the rebuy"
        assert open_date == "2026-08-27"

    def test_an_overnight_add_covers_neither(self):
        """Overnight 25 plus a 10 add today: 25 of the 35 held are NOT today's."""
        fills = [
            _fill(side="BOT", shares=10, price=6.00, avg_price=6.00,
                  time="2026-08-27T15:00:00"),
        ]
        basis = ib_sync.build_fill_basis(fills)
        key = next(iter(basis))
        assert ib_sync._session_fill_cover(basis[key], 35) is None
        assert ib_sync._session_fill_open_date(basis[key], 35) is None

    def test_a_genuine_same_day_open_still_gets_both(self):
        fills = [
            _fill(side="BOT", shares=25, price=5.55, avg_price=5.55,
                  time="2026-08-27T14:49:55"),
        ]
        basis = ib_sync.build_fill_basis(fills)
        key = next(iter(basis))
        assert ib_sync._session_fill_cover(basis[key], 25) is not None
        assert ib_sync._session_fill_open_date(basis[key], 25) == "2026-08-27"

    def test_overnight_inventory_gets_neither(self):
        fills = [
            _fill(side="BOT", shares=10, price=5.55, avg_price=5.55,
                  time="2026-08-27T14:49:55"),
        ]
        basis = ib_sync.build_fill_basis(fills)
        key = next(iter(basis))
        assert ib_sync._session_fill_cover(basis[key], 25) is None
        assert ib_sync._session_fill_open_date(basis[key], 25) is None


class TestMultiplierIsCarried:
    """R-373: an ES-style or corporate-action-adjusted contract is not x100."""

    def test_a_fifty_multiplier_contract_is_not_valued_at_a_hundred(self):
        fills = [
            _fill(side="BOT", shares=2, price=10.0, avg_price=10.0,
                  multiplier="50", time="2026-08-27T14:49:55"),
        ]
        basis = ib_sync.build_fill_basis(fills)
        key = next(iter(basis))
        entry_cost, _avg = ib_sync._session_fill_cover(basis[key], 2)
        assert entry_cost == pytest.approx(1000.0), (
            f"2 x $10.00 x 50 = $1,000, not $2,000; got {entry_cost}"
        )

    def test_the_default_hundred_multiplier_is_unchanged(self):
        fills = [
            _fill(side="BOT", shares=2, price=10.0, avg_price=10.0,
                  time="2026-08-27T14:49:55"),
        ]
        basis = ib_sync.build_fill_basis(fills)
        key = next(iter(basis))
        entry_cost, _avg = ib_sync._session_fill_cover(basis[key], 2)
        assert entry_cost == pytest.approx(2000.0)

    def test_an_unreadable_multiplier_falls_back_to_a_hundred(self):
        fills = [
            _fill(side="BOT", shares=2, price=10.0, avg_price=10.0,
                  multiplier="", time="2026-08-27T14:49:55"),
        ]
        basis = ib_sync.build_fill_basis(fills)
        key = next(iter(basis))
        entry_cost, _avg = ib_sync._session_fill_cover(basis[key], 2)
        assert entry_cost == pytest.approx(2000.0)


class TestMixedLegBasisIsNamed:
    """R-374: a partially-rolled structure must not present a blended basis."""

    def test_one_rolled_leg_marks_the_position_mixed(self):
        legs = [
            {"basis_source": "session_fills"},
            {"basis_source": None},
        ]
        assert ib_sync._position_basis_source(legs) == "mixed", (
            "total_entry_cost then sums today's session VWAP with IB's lagged "
            "avgCost, and max_risk for a defined-risk structure inherits it; "
            "None was indistinguishable from 'no session fills at all'"
        )

    def test_every_leg_covered_is_still_session_fills(self):
        legs = [{"basis_source": "session_fills"}, {"basis_source": "session_fills"}]
        assert ib_sync._position_basis_source(legs) == "session_fills"

    def test_no_leg_covered_is_still_none(self):
        assert ib_sync._position_basis_source([{"basis_source": None}] * 2) is None

    def test_no_legs_is_none(self):
        assert ib_sync._position_basis_source([]) is None


class TestMixedBasisRefusesToAggregate:
    """T-253: naming the blend is not enough — the blend must not ship.

    Roll the short leg of a debit vertical intraday and hold the long leg
    overnight: the rolled leg takes today's session VWAP, the held leg keeps
    IB's lagged avgCost, and `total_entry_cost` sums them into a number that
    corresponds to no actual trade. `max_risk` for a defined-risk structure is
    that same number, and Gate 3 sizes the 2.5% bankroll cap off it.
    """

    EXPIRY = "20260828"

    def _position(self, *, strike, right, qty, ib_avg_cost):
        contract = SimpleNamespace(
            symbol="META", secType="OPT", strike=strike, right=right,
            lastTradeDateOrContractMonth=self.EXPIRY, multiplier="100",
            currency="USD", conId=int(strike) * 10 + (1 if right == "C" else 2),
        )
        return SimpleNamespace(
            contract=contract, position=qty, avgCost=ib_avg_cost, account="U1",
        )

    def _collapse_partially_rolled_vertical(self):
        """Long 575C on yesterday's IB basis, short 580C rolled today."""
        client = SimpleNamespace(get_positions=lambda: [
            self._position(strike=575.0, right="C", qty=10, ib_avg_cost=500.0),
            self._position(strike=580.0, right="C", qty=-10, ib_avg_cost=300.0),
        ])
        # Only the SHORT leg was traded this session.
        rolled = [
            _fill(side="SLD", shares=10, price=4.00, avg_price=4.00,
                  strike=580.0, expiry=self.EXPIRY,
                  time="2026-08-28T10:15:00"),
        ]
        positions = ib_sync.fetch_positions(
            client, fill_basis_lookup=ib_sync.build_fill_basis(rolled)
        )
        return positions, ib_sync.collapse_positions(positions)

    def test_only_one_leg_is_on_session_basis(self):
        positions, _ = self._collapse_partially_rolled_vertical()
        by_strike = {p["strike"]: p for p in positions}
        assert by_strike[580.0]["basis_source"] == "session_fills"
        assert by_strike[575.0]["basis_source"] == "ib"

    def test_a_blended_entry_cost_is_not_published(self):
        _, collapsed = self._collapse_partially_rolled_vertical()
        [pos] = collapsed
        assert pos["basis_source"] == "mixed"
        assert pos["entry_cost"] is None, (
            "$5,000 of yesterday's IB avgCost minus $4,000 of today's session "
            "VWAP is a basis for a trade that was never placed"
        )

    def test_max_risk_is_not_derived_from_a_blended_basis(self):
        _, collapsed = self._collapse_partially_rolled_vertical()
        [pos] = collapsed
        assert pos["risk_profile"] == "defined"
        assert pos["max_risk"] is None, (
            "Gate 3 sizes the 2.5% bankroll cap off max_risk"
        )

    def test_an_all_session_vertical_still_publishes_its_basis(self):
        """The refusal is scoped to `mixed` — a fully covered structure is fine."""
        client = SimpleNamespace(get_positions=lambda: [
            self._position(strike=575.0, right="C", qty=10, ib_avg_cost=500.0),
            self._position(strike=580.0, right="C", qty=-10, ib_avg_cost=300.0),
        ])
        fills = [
            _fill(side="BOT", shares=10, price=6.00, avg_price=6.00,
                  strike=575.0, expiry=self.EXPIRY, time="2026-08-28T10:15:00"),
            _fill(side="SLD", shares=10, price=4.00, avg_price=4.00,
                  strike=580.0, expiry=self.EXPIRY, time="2026-08-28T10:15:00"),
        ]
        positions = ib_sync.fetch_positions(
            client, fill_basis_lookup=ib_sync.build_fill_basis(fills)
        )
        [pos] = ib_sync.collapse_positions(positions)
        assert pos["basis_source"] == "session_fills"
        assert pos["entry_cost"] == pytest.approx(2000.0)
        assert pos["max_risk"] == pytest.approx(2000.0)


class TestDeployedCapitalNamesWhatItCannotMeasure:
    """T-253 lead follow-up. Refusing to publish a blended `entry_cost` makes
    `total_deployed_dollars` an UNDER-statement, and therefore
    `remaining_capacity_pct` an OVER-statement — the unsafe direction for
    Gate 3's 2.5% cap. The omission must be countable, never silent."""

    @staticmethod
    def _payload(entry_costs):
        import ib_sync

        collapsed = [
            {
                "id": f"p{i}",
                "entry_cost": cost,
                "risk_profile": "defined",
            }
            for i, cost in enumerate(entry_costs)
        ]
        return ib_sync.convert_to_portfolio_format(
            {"NetLiquidation": 100_000.0}, collapsed, {}
        )

    def test_a_fully_measured_book_reports_no_omissions(self):
        payload = self._payload([1_000.0, 4_000.0])

        assert payload["unmeasured_basis_count"] == 0
        assert payload["total_deployed_dollars"] == 5_000.0
        assert payload["remaining_capacity_pct"] == 95.0

    def test_a_mixed_basis_position_is_counted_not_silently_dropped(self):
        payload = self._payload([1_000.0, None, 4_000.0])

        assert payload["unmeasured_basis_count"] == 1, (
            "a position with no measurable basis vanished from deployed "
            "capital without being counted, so remaining_capacity_pct reads "
            "as a fact when it is a ceiling"
        )
        # The measured floor is still published, and still honest about being
        # a floor by virtue of the count above.
        assert payload["total_deployed_dollars"] == 5_000.0
