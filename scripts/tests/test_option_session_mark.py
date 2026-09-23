"""Adversarial tests for after-hours option last vs previous-session close.

Live evidence 2026-09-21 20:22 ET: META 16 Oct 2026 665 put Last Price C$26.70.
IB snapshot: last=None, bid=ask=-1, close=26.70. 1-minute TRADES last print
8.10 at 15:59 ET (volume 50); 15:59 midpoint 8.15. Trailing empty bars
forward-fill 6.95 at volume 0 and must not beat the print.
"""

from pathlib import Path

import pytest

from option_session_mark import (
    last_midpoint_bar_price,
    last_traded_bar_price,
    load_session_marks,
    option_mark_key,
    poll_contract_mark,
    resolve_polled_mark,
    save_session_marks,
    session_is_fresh,
)

TODAY = "2026-09-21"
META_KEY = "META_20261016_665_P"
CLOSE = 26.70
PRINT = 8.10
MID = 8.15


def _bars(*rows):
    return [{"close": close, "volume": volume} for close, volume in rows]


class TestOptionMarkKey:
    def test_meta_put_matches_relay_symbol(self):
        assert option_mark_key("META", "2026-10-16", 665, "P") == META_KEY

    def test_integer_strike_drops_decimal(self):
        assert option_mark_key("META", "20261016", 665.0, "p") == META_KEY

    def test_half_strike_keeps_fraction(self):
        assert option_mark_key("SPY", "20261016", 662.5, "C") == "SPY_20261016_662.5_C"

    def test_rejects_junk(self):
        assert option_mark_key("", "20261016", 665, "P") is None
        assert option_mark_key("META", "bad", 665, "P") is None
        assert option_mark_key("META", "20261016", 0, "P") is None


class TestBarParsers:
    def test_last_volume_bar_is_the_print_not_a_forward_fill(self):
        bars = _bars(
            (13.53, 12),
            (6.95, 10),
            (6.95, 0),
            (6.95, 0),
            (PRINT, 50),
        )
        assert last_traded_bar_price(bars) == PRINT

    def test_trailing_zero_volume_close_does_not_win(self):
        bars = _bars((PRINT, 50), (CLOSE, 0), (CLOSE, 0))
        assert last_traded_bar_price(bars) == PRINT

    def test_all_empty_trades_are_unusable(self):
        assert last_traded_bar_price(_bars((CLOSE, 0), (0, 0))) is None
        assert last_traded_bar_price([]) is None
        assert last_traded_bar_price(None) is None

    def test_sentinel_close_is_ignored(self):
        assert last_traded_bar_price(_bars((1.7976931348623157e308, 10))) is None

    def test_midpoint_uses_last_positive_close(self):
        bars = _bars((7.55, 0), (MID, 0))
        assert last_midpoint_bar_price(bars) == MID


class TestResolvePolledMark:
    def test_option_previous_close_is_not_a_mark(self):
        price, calc = resolve_polled_mark(None, None, None, CLOSE, sec_type="OPT")
        assert price is None
        assert calc is False

    def test_stock_still_uses_close(self):
        price, calc = resolve_polled_mark(None, None, None, 741.24, sec_type="STK")
        assert price == 741.24
        assert calc is True

    def test_live_last_beats_close_and_session(self):
        price, calc = resolve_polled_mark(
            9.0, 8.9, 9.1, CLOSE,
            sec_type="OPT",
            session_mark={"last": PRINT, "checked_on": TODAY},
            session_is_fresh=True,
        )
        assert price == 9.0
        assert calc is False

    def test_live_book_beats_session_last(self):
        price, calc = resolve_polled_mark(
            None, 8.0, 8.3, CLOSE,
            sec_type="OPT",
            session_mark={"last": PRINT},
            session_is_fresh=True,
        )
        assert price == 8.15
        assert calc is True

    def test_fresh_session_last_beats_close_without_history(self):
        price, calc = resolve_polled_mark(
            None, None, None, CLOSE,
            sec_type="OPT",
            session_mark={"last": PRINT, "checked_on": TODAY},
            session_is_fresh=True,
            trade_bars=_bars((99, 1)),
        )
        assert price == PRINT
        assert calc is False

    def test_history_print_beats_stale_cached_close_shaped_last(self):
        price, calc = resolve_polled_mark(
            None, None, None, CLOSE,
            sec_type="OPT",
            session_mark={"last": CLOSE, "checked_on": "2026-09-20"},
            session_is_fresh=False,
            trade_bars=_bars((PRINT, 50), (CLOSE, 0)),
        )
        assert price == PRINT
        assert calc is False

    def test_stale_session_last_survives_empty_history(self):
        price, calc = resolve_polled_mark(
            None, None, None, CLOSE,
            sec_type="OPT",
            session_mark={"last": PRINT, "checked_on": "2026-09-20"},
            session_is_fresh=False,
            trade_bars=_bars((CLOSE, 0)),
        )
        assert price == PRINT
        assert calc is False

    def test_session_book_mid_when_no_print(self):
        price, calc = resolve_polled_mark(
            None, None, None, CLOSE,
            sec_type="OPT",
            session_mark={"bid": 8.0, "ask": 8.3, "checked_on": TODAY},
            session_is_fresh=True,
        )
        assert price == 8.15
        assert calc is True

    def test_history_midpoint_when_no_trades(self):
        price, calc = resolve_polled_mark(
            None, None, None, CLOSE,
            sec_type="OPT",
            session_is_fresh=False,
            trade_bars=_bars((CLOSE, 0)),
            midpoint_bars=_bars((MID, 0)),
        )
        assert price == MID
        assert calc is True

    def test_one_sided_book_is_not_a_mid(self):
        price, calc = resolve_polled_mark(None, None, 40.0, CLOSE, sec_type="OPT")
        assert price is None
        assert calc is False


class TestPollContractMark:
    def test_meta_shape_uses_history_print_not_close(self):
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return _bars((6.95, 10), (6.95, 0), (PRINT, 50)), _bars((MID, 0))

        price, calc, updated = poll_contract_mark(
            sec_type="OPT",
            market_price=None,
            bid=None,
            ask=None,
            close=CLOSE,
            trade=None,
            session_mark=None,
            today=TODAY,
            fetch_history=fetch,
        )
        assert calls["n"] == 1
        assert price == PRINT
        assert calc is False
        assert updated["last"] == PRINT
        assert updated["checked_on"] == TODAY

    def test_fresh_cache_does_not_call_history(self):
        def fetch():
            raise AssertionError("history must not run when checked today")

        price, calc, updated = poll_contract_mark(
            sec_type="OPT",
            market_price=None,
            bid=None,
            ask=None,
            close=CLOSE,
            trade=None,
            session_mark={"last": PRINT, "checked_on": TODAY},
            today=TODAY,
            fetch_history=fetch,
        )
        assert price == PRINT
        assert calc is False
        assert updated is None

    def test_history_error_falls_back_to_stale_last_not_close(self):
        def fetch():
            raise TimeoutError("pacing")

        price, calc, updated = poll_contract_mark(
            sec_type="OPT",
            market_price=None,
            bid=None,
            ask=None,
            close=CLOSE,
            trade=None,
            session_mark={"last": PRINT, "checked_on": "2026-09-20"},
            today=TODAY,
            fetch_history=fetch,
        )
        assert price == PRINT
        assert calc is False
        assert updated is None

    @pytest.mark.parametrize("failure", ["throw", "empty", "no_volume"])
    @pytest.mark.parametrize("cached", [{"last": PRINT}, {"mid": PRINT}])
    def test_failed_history_keeps_provenance_and_allows_next_poll(self, failure, cached):
        old = {**cached, "checked_on": "2026-09-20"}
        calls = []

        def fetch():
            calls.append(1)
            if len(calls) == 1:
                if failure == "throw":
                    raise TimeoutError("history unavailable")
                if failure == "no_volume":
                    return _bars((99, 0)), []
                return [], []
            return _bars((15, 1)), []

        kwargs = dict(sec_type="OPT", market_price=None, bid=None, ask=None,
                      close=CLOSE, trade=None, today=TODAY, fetch_history=fetch)
        price, _, updated = poll_contract_mark(session_mark=old, **kwargs)
        assert price == PRINT
        assert updated is None
        assert old["checked_on"] == "2026-09-20"
        price, calculated, updated = poll_contract_mark(session_mark=old, **kwargs)
        assert len(calls) == 2
        assert price == 15
        assert calculated is False
        assert updated["last"] == 15
        assert updated["checked_on"] == TODAY

    @pytest.mark.parametrize("live_book", [False, True])
    def test_new_midpoint_does_not_promote_old_trade(self, live_book):
        kwargs = dict(sec_type="OPT", market_price=None, bid=None, ask=None,
                      close=CLOSE, trade=None, today=TODAY)
        _, _, updated = poll_contract_mark(
            **{**kwargs, "bid": 14 if live_book else None, "ask": 16 if live_book else None},
            session_mark={"last": PRINT, "checked_on": "2026-09-20"},
            fetch_history=lambda: ([], _bars((15, 0))),
        )
        price, calculated, _ = poll_contract_mark(session_mark=updated, **kwargs)
        assert price == 15
        assert calculated is True

    def test_sync_recovery_updates_position_value(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from ib_sync import _stamp_position_price

        client = Mock()
        client.get_historical_data.side_effect = [TimeoutError(), TimeoutError(), _bars((15, 1))]
        ticker = SimpleNamespace(marketPrice=lambda: None, bid=None, ask=None, close=CLOSE, last=None)
        pos = dict(secType="OPT", symbol="META", expiry="20261016", strike=665,
                   right="P", position=2, contract=SimpleNamespace(symbol="META"))
        marks = {META_KEY: {"last": PRINT, "checked_on": "2026-09-20"}}
        assert _stamp_position_price(pos, ticker, marks, client, TODAY) is False
        assert pos["marketValue"] == 1620
        assert _stamp_position_price(pos, ticker, marks, client, TODAY) is True
        assert pos["marketPrice"] == 15
        assert pos["marketValue"] == 3000
        assert client.get_historical_data.call_count == 3
        assert all(call.kwargs["timeout"] == 5.0 for call in client.get_historical_data.call_args_list)

    def test_live_ticker_last_is_what_gets_cached(self):
        price, calc, updated = poll_contract_mark(
            sec_type="OPT",
            market_price=8.15,
            bid=8.0,
            ask=8.3,
            close=CLOSE,
            trade=PRINT,
            session_mark=None,
            today=TODAY,
            fetch_history=lambda: (_bars((99, 1)), None),
        )
        assert price == 8.15
        assert calc is False
        assert updated["last"] == PRINT
        assert updated["bid"] == 8.0
        assert updated["ask"] == 8.3

    def test_stock_does_not_write_option_cache(self):
        price, calc, updated = poll_contract_mark(
            sec_type="STK",
            market_price=None,
            bid=None,
            ask=None,
            close=741.24,
            trade=None,
            session_mark=None,
            today=TODAY,
        )
        assert price == 741.24
        assert calc is True
        assert updated is None

    def test_fetcher_not_used_when_live_book_exists(self):
        def fetch():
            raise AssertionError("live book")

        price, calc, updated = poll_contract_mark(
            sec_type="OPT",
            market_price=None,
            bid=8.0,
            ask=8.3,
            close=CLOSE,
            trade=None,
            session_mark=None,
            today=TODAY,
            fetch_history=fetch,
        )
        assert price == 8.15
        assert calc is True
        assert updated["bid"] == 8.0
        assert updated["ask"] == 8.3


class TestCacheRoundtrip:
    def test_roundtrip_and_corrupt_file(self, tmp_path: Path):
        path = tmp_path / "option_session_mark_cache.json"
        marks = {META_KEY: {"last": PRINT, "bid": 8.0, "ask": 8.3, "checked_on": TODAY}}
        save_session_marks(marks, path)
        loaded = load_session_marks(path)
        assert loaded[META_KEY]["last"] == PRINT
        assert session_is_fresh(loaded[META_KEY], TODAY) is True
        path.write_text("{not json")
        assert load_session_marks(path) == {}
        assert load_session_marks(tmp_path / "missing.json") == {}

    def test_does_not_persist_close_as_last(self, tmp_path: Path):
        path = tmp_path / "cache.json"
        save_session_marks({META_KEY: {"last": 0, "close": CLOSE}}, path)
        assert load_session_marks(path) == {}
