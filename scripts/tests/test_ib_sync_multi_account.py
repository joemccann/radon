from __future__ import annotations

import pytest

import ib_sync


def test_positions_never_cross_account_financial_state():
    with pytest.raises(ValueError, match="multiple managed accounts"):
        ib_sync._select_managed_account(["DU111", "DU222"], "")

    assert ib_sync._select_managed_account(["DU111", "DU222"], "DU222") == "DU222"


def test_unknown_explicit_account_fails_closed():
    with pytest.raises(ValueError, match="not managed"):
        ib_sync._select_managed_account(["DU111"], "DU999")
