"""Regression tests for equity option-chain selection.

IB can return adjusted trading classes before the standard class for the same
underlying and expiry. The chain endpoint must not let that first record define
the visible strike book.
"""

from types import SimpleNamespace

from scripts import ib_option_chain


def _chain(exchange: str, trading_class: str, expirations: list[str], strikes: list[float]):
    return SimpleNamespace(
        exchange=exchange,
        tradingClass=trading_class,
        expirations=expirations,
        strikes=strikes,
        multiplier="100",
    )


def test_requested_expiry_prefers_canonical_trading_class_over_adjusted_chain():
    chains = [
        _chain("SMART", "2MSFT", ["20260717"], [380.0, 400.0, 410.0, 430.0]),
        _chain("BOX", "MSFT", ["20260717"], [350.0, 352.5, 355.0]),
        _chain("SMART", "MSFT", ["20260717"], [352.5, 355.0, 357.5, 360.0]),
    ]

    selected = ib_option_chain._chains_for_expiry(chains, "MSFT", "2026-07-17")

    assert [chain.tradingClass for chain in selected] == ["MSFT", "MSFT"]
    assert ib_option_chain._preferred_exchange(selected) == "SMART"
    assert ib_option_chain._union_strikes(selected) == [350.0, 352.5, 355.0, 357.5, 360.0]


def test_requested_expiry_falls_back_to_adjusted_chain_when_no_canonical_chain_exists():
    chains = [
        _chain("SMART", "2MSFT", ["20260717"], [380.0, 400.0]),
        _chain("CBOE", "2MSFT", ["20260717"], [400.0, 410.0]),
    ]

    selected = ib_option_chain._chains_for_expiry(chains, "MSFT", "20260717")

    assert [chain.tradingClass for chain in selected] == ["2MSFT", "2MSFT"]
    assert ib_option_chain._union_strikes(selected) == [380.0, 400.0, 410.0]


def test_expiration_list_filters_adjusted_chains_when_standard_chain_exists():
    chains = [
        _chain("SMART", "2MSFT", ["20260728"], [380.0]),
        _chain("BOX", "MSFT", ["20260717", "20260724"], [350.0, 355.0]),
    ]

    selected = ib_option_chain._canonical_trading_class_chains(chains, "MSFT")
    expirations = sorted(
        expiry
        for chain in selected
        for expiry in ib_option_chain._chain_expirations(chain)
    )

    assert expirations == ["20260717", "20260724"]
