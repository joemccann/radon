from types import SimpleNamespace

import pytest

import ib_place_order


def option_contract(**overrides):
    values = {
        "symbol": "SPX",
        "secType": "OPT",
        "lastTradeDateOrContractMonth": "20260918",
        "strike": 7000.0,
        "right": "C",
        "exchange": "CBOE",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_conid_must_match_every_submitted_contract_attribute():
    params = {
        "symbol": "SPX", "expiry": "20260918", "strike": 7000,
        "right": "C", "exchange": "CBOE",
    }
    assert ib_place_order._contract_identity_mismatch(params, option_contract(), "OPT") is None

    for field, value in [
        ("symbol", "NDX"), ("secType", "FUT"),
        ("lastTradeDateOrContractMonth", "20260925"),
        ("strike", 7100), ("right", "P"), ("exchange", "SMART"),
    ]:
        mismatch = ib_place_order._contract_identity_mismatch(
            params, option_contract(**{field: value}), "OPT",
        )
        assert mismatch is not None, field
