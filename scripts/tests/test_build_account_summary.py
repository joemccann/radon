"""
TDD: build_account_summary — all IB Portal balance fields are emitted.

Verifies that build_account_summary() produces all fields matching IB Portal:
- net_liquidation, daily_pnl, unrealized_pnl, realized_pnl
- cash, settled_cash
- maintenance_margin, initial_margin, excess_liquidity
- buying_power, available_funds
- dividends
- equity_with_loan, previous_day_ewl, reg_t_equity, sma
- gross_position_value
"""

import sys
from pathlib import Path

# Add scripts directory to path so we can import ib_sync
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_build_account_summary_contains_all_ib_portal_fields():
    """All IB Portal balance fields appear in build_account_summary output."""
    from ib_sync import build_account_summary

    account = {
        'NetLiquidation': 1_156_681.47,
        'TotalCashValue': 251_787.68,
        'SettledCash': 251_787.68,
        'UnrealizedPnL': -309_278.31,
        'RealizedPnL': 2_383.09,
        'MaintMarginReq': 247_944.42,
        'InitMarginReq': 247_944.42,
        'ExcessLiquidity': 524_030.98,
        'BuyingPower': 2_091_400.00,
        'AvailableFunds': 523_898.09,
        'NetDividend': 0.0,
        'EquityWithLoanValue': 771_842.51,
        'PreviousDayEquityWithLoanValue': 770_794.42,
        'RegTEquity': 772_192.93,
        'SMA': 694_731.30,
        'GrossPositionValue': 1_145_674.10,
    }
    pnl_data = {
        'dailyPnL': 2_660.00,
        'unrealizedPnL': -309_278.31,
        'realizedPnL': 2_383.09,
    }

    result = build_account_summary(account, pnl_data)

    # All IB Portal fields must be present
    expected_fields = [
        'net_liquidation', 'daily_pnl', 'unrealized_pnl', 'realized_pnl',
        'cash', 'settled_cash',
        'maintenance_margin', 'initial_margin', 'excess_liquidity',
        'buying_power', 'available_funds',
        'dividends',
        'equity_with_loan', 'previous_day_ewl', 'reg_t_equity', 'sma',
        'gross_position_value',
    ]
    for field in expected_fields:
        assert field in result, f"Missing field: {field}"


def test_build_account_summary_values_match_ib_portal():
    """Values match IB Portal ground truth."""
    from ib_sync import build_account_summary

    account = {
        'NetLiquidation': 1_156_681.47,
        'TotalCashValue': 251_787.68,
        'SettledCash': 251_787.68,
        'UnrealizedPnL': -309_278.31,
        'RealizedPnL': 2_383.09,
        'MaintMarginReq': 247_944.42,
        'InitMarginReq': 247_944.42,
        'ExcessLiquidity': 524_030.98,
        'BuyingPower': 2_091_400.00,
        'AvailableFunds': 523_898.09,
        'NetDividend': 0.0,
        'EquityWithLoanValue': 771_842.51,
        'PreviousDayEquityWithLoanValue': 770_794.42,
        'RegTEquity': 772_192.93,
        'SMA': 694_731.30,
        'GrossPositionValue': 1_145_674.10,
    }
    pnl_data = {
        'dailyPnL': 2_660.00,
        'unrealizedPnL': -309_278.31,
        'realizedPnL': 2_383.09,
    }

    result = build_account_summary(account, pnl_data)

    assert result['net_liquidation'] == 1_156_681.47
    assert result['daily_pnl'] == 2_660.00
    assert result['unrealized_pnl'] == -309_278.31
    assert result['realized_pnl'] == 2_383.09
    assert result['cash'] == 251_787.68
    assert result['settled_cash'] == 251_787.68
    assert result['maintenance_margin'] == 247_944.42
    assert result['initial_margin'] == 247_944.42
    assert result['excess_liquidity'] == 524_030.98
    assert result['buying_power'] == 2_091_400.00
    assert result['available_funds'] == 523_898.09
    assert result['dividends'] == 0.0
    assert result['equity_with_loan'] == 771_842.51
    assert result['previous_day_ewl'] == 770_794.42
    assert result['reg_t_equity'] == 772_192.93
    assert result['sma'] == 694_731.30
    assert result['gross_position_value'] == 1_145_674.10


def test_build_account_summary_settled_cash_fallback():
    """When SettledCash is missing, settled_cash falls back to TotalCashValue."""
    from ib_sync import build_account_summary

    account = {
        'NetLiquidation': 100_000.0,
        'TotalCashValue': 50_000.0,
        # No 'SettledCash' key
    }
    pnl_data = {}

    result = build_account_summary(account, pnl_data)

    assert result['cash'] == 50_000.0
    assert result['settled_cash'] == 50_000.0  # Falls back to TotalCashValue


def test_build_account_summary_missing_new_fields_default_zero():
    """New fields default to 0.0 when IB tags are missing (backward compat)."""
    from ib_sync import build_account_summary

    account = {
        'NetLiquidation': 100_000.0,
        'TotalCashValue': 50_000.0,
    }
    pnl_data = {}

    result = build_account_summary(account, pnl_data)

    assert result['equity_with_loan'] == 0.0
    assert result['previous_day_ewl'] == 0.0
    assert result['reg_t_equity'] == 0.0
    assert result['sma'] == 0.0
    assert result['gross_position_value'] == 0.0
    assert result['available_funds'] == 0.0
    assert result['initial_margin'] == 0.0


def test_build_account_summary_daily_pnl_none_when_unavailable():
    """daily_pnl is None (not 0) when reqPnL data is unavailable."""
    from ib_sync import build_account_summary

    account = {'NetLiquidation': 100_000.0, 'TotalCashValue': 50_000.0}
    pnl_data = {}  # No dailyPnL

    result = build_account_summary(account, pnl_data)

    assert result['daily_pnl'] is None
