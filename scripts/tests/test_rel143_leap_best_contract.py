"""R-387 / REL-143(a): the LEAP the scanner promotes is one that can be traded.

`pick_best_mispriced_leap` is `max(leaps, key=lambda l: (hv_20 - l.iv, l.oi))`
and `hv_20` is CONSTANT across the group, so it is purely argmin(IV) -- `oi`
breaks only an exact tie on an IV rounded to 2dp, which essentially never
happens, and the docstring's "ties go to the deeper open interest" is
inoperative. `get_leap_options` rejects only `iv == 0`, so `oi=0` / `volume=0`
contracts survive into the pool, and the lowest quoted IV in a delta bucket is
systematically the stalest or least-traded quote. That contract is then
serialised as `best_leap` and deep-linked as the headline TRADE BEST order.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from leap_scanner_uw import LeapOption, pick_best_mispriced_leap  # noqa: E402


def _leap(iv: float, oi: int, volume: int = 0) -> LeapOption:
    return LeapOption(
        symbol=f"SPY270115C{int(iv * 1000):08d}",
        expiry="2027-01-15",
        strike=600.0,
        right="C",
        iv=iv,
        volume=volume,
        oi=oi,
        delta_approx=0.5,
    )


class TestTheHeadlineContractIsTradeable:
    def test_a_dead_quote_does_not_win_on_iv_alone(self):
        """iv=12 with zero open interest is the stalest quote, not the best edge."""
        dead = _leap(12.0, oi=0, volume=0)
        liquid = _leap(22.0, oi=4200, volume=310)
        assert pick_best_mispriced_leap([dead, liquid], hv_20=30.0) is liquid

    def test_an_all_illiquid_group_promotes_nothing(self):
        """No tradeable contract means no TRADE BEST link, not a stale one."""
        group = [_leap(12.0, oi=0), _leap(18.0, oi=0, volume=0)]
        assert pick_best_mispriced_leap(group, hv_20=30.0) is None

    def test_volume_alone_qualifies_a_fresh_quote(self):
        """A contract trading today is tradeable even with thin resting OI."""
        fresh = _leap(14.0, oi=0, volume=95)
        assert pick_best_mispriced_leap([fresh], hv_20=30.0) is fresh

    def test_among_liquid_contracts_the_widest_gap_still_wins(self):
        narrow = _leap(25.0, oi=5000, volume=100)
        wide = _leap(15.0, oi=1200, volume=40)
        assert pick_best_mispriced_leap([narrow, wide], hv_20=30.0) is wide

    def test_ties_really_do_go_to_the_deeper_open_interest(self):
        """The docstring's promise, now reachable."""
        thin = _leap(20.0, oi=100, volume=10)
        deep = _leap(20.0, oi=9000, volume=10)
        assert pick_best_mispriced_leap([thin, deep], hv_20=30.0) is deep

    def test_an_empty_group_is_still_none(self):
        assert pick_best_mispriced_leap([], hv_20=30.0) is None


class TestBestLeapComesFromTheGroupThatSetBestGap:
    def test_a_gap60_only_ticker_does_not_arm_a_contract(self, monkeypatch):
        """`best_leap` was assigned under `gap_20 > best_gap or best_leap is None`.

        A ticker mispriced only via `gap_60` therefore got a `best_leap` while
        `best_gap` stayed at its `0` initialiser, rendering a MISPRICED row whose
        anchor text is `+0.0` while still arming a live contract. R-388.
        """
        import leap_scanner_uw as mod

        source = "\n".join(
            line for line in Path(mod.__file__).read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "if gap_20 > best_gap or best_leap is None:" not in source, (
            "best_leap is still assignable from a group that did not set best_gap"
        )
