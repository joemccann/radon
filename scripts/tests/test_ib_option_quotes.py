"""IB option quote snapshot: strike selection and ticker -> chat contract rows."""

from types import SimpleNamespace

from scripts import ib_option_quotes

NAN = float("nan")


def _ticker(strike, right, *, bid, ask, last=NAN, volume=NAN, greeks=None, expiry="20261030"):
    return SimpleNamespace(
        contract=SimpleNamespace(strike=strike, right=right, lastTradeDateOrContractMonth=expiry),
        bid=bid,
        ask=ask,
        last=last,
        volume=volume,
        modelGreeks=greeks,
        lastGreeks=None,
    )


def test_select_strikes_keeps_nearest_to_spot_in_strike_order():
    strikes = [140.0, 145.0, 150.0, 155.0, 160.0, 165.0, 170.0, 175.0]
    assert ib_option_quotes.select_strikes(strikes, spot=158.65, wings=1) == [155.0, 160.0, 165.0]


def test_select_strikes_without_spot_takes_the_middle_of_the_book():
    strikes = [float(s) for s in range(100, 200, 10)]
    assert ib_option_quotes.select_strikes(strikes, spot=None, wings=1) == [140.0, 150.0, 160.0]


def test_quote_row_maps_ib_ticker_to_chat_contract_shape():
    greeks = SimpleNamespace(impliedVol=0.448, delta=0.56, gamma=0.021, theta=-0.18, vega=0.19)
    row = ib_option_quotes.quote_row(_ticker(160.0, "C", bid=7.0, ask=7.2, last=7.1, volume=466.0, greeks=greeks))
    assert row == {
        "strike": 160.0,
        "right": "C",
        "expiry": "2026-10-30",
        "bid": 7.0,
        "ask": 7.2,
        "mid": 7.1,
        "last": 7.1,
        "iv": 0.448,
        "oi": None,
        "volume": 466,
        "delta": 0.56,
        "gamma": 0.021,
        "theta": -0.18,
        "vega": 0.19,
    }


def test_quote_row_drops_ib_sentinels_and_one_sided_books():
    row = ib_option_quotes.quote_row(_ticker(200.0, "P", bid=-1.0, ask=1.5, greeks=None))
    assert row["bid"] is None
    assert row["ask"] == 1.5
    assert row["mid"] is None
    assert row["iv"] is None
    assert row["delta"] is None
    assert row["volume"] is None


def test_quote_row_falls_back_to_last_greeks():
    last = SimpleNamespace(impliedVol=0.5, delta=-0.3, gamma=0.01, theta=-0.1, vega=0.2)
    t = _ticker(150.0, "P", bid=3.0, ask=3.2)
    t.lastGreeks = last
    assert ib_option_quotes.quote_row(t)["delta"] == -0.3


def test_spot_prefers_last_then_mid_then_close():
    assert ib_option_quotes.spot_from_ticker(SimpleNamespace(last=158.65, bid=158.6, ask=158.7, close=148.0)) == 158.65
    assert ib_option_quotes.spot_from_ticker(SimpleNamespace(last=NAN, bid=158.6, ask=158.7, close=148.0)) == 158.65
    assert ib_option_quotes.spot_from_ticker(SimpleNamespace(last=NAN, bid=NAN, ask=NAN, close=148.0)) == 148.0
    assert ib_option_quotes.spot_from_ticker(None) is None
