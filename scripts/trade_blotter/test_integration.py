#!/usr/bin/env python3
"""
Integration tests for Trade Blotter - runs against live IB connection.

Run with: python3 test_integration.py

These tests require:
- IB Gateway or TWS running
- API connections enabled on port 4001 (Gateway) or 7497 (TWS)
"""
import sys
import os

import pytest

# These smoke checks require a real Gateway. The default repository gate
# excludes integration tests; keep live connectivity an explicit opt-in.
pytestmark = pytest.mark.integration
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from decimal import Decimal
from blotter_service import create_blotter_service, IBFetcher
from models import SecurityType


def test_ib_connection():
    """Test basic IB connection."""
    print("Testing IB connection...")
    
    fetcher = IBFetcher(host="127.0.0.1", port=4001, client_id=87)
    
    try:
        fetcher._connect()
        connected = fetcher.ib.isConnected()
        fetcher._disconnect()
        
        assert connected, "Failed to connect to IB"
        print("  ✅ IB connection successful")
        return True
    except Exception as e:
        print(f"  ❌ IB connection failed: {e}")
        return False


def test_fetch_today_executions():
    """Test fetching today's executions."""
    print("\nTesting execution fetch...")
    
    service = create_blotter_service(
        source="ib",
        host="127.0.0.1",
        port=4001,
        client_id=86,
    )
    
    try:
        blotter = service.build_blotter()
        
        print(f"  ✅ Fetched {len(blotter.trades)} trades")
        print(f"     Open: {len(blotter.open_trades)}")
        print(f"     Closed: {len(blotter.closed_trades)}")
        print(f"     Total commissions: ${blotter.total_commissions:.2f}")
        
        return True
    except Exception as e:
        print(f"  ❌ Fetch failed: {e}")
        return False


def test_spread_grouping():
    """Test spread identification from live data."""
    print("\nTesting spread grouping...")
    
    service = create_blotter_service(
        source="ib",
        host="127.0.0.1", 
        port=4001,
        client_id=85,
    )
    
    try:
        blotter = service.build_blotter()
        spreads = blotter.get_spreads()
        
        print(f"  ✅ Identified {len(spreads)} spreads")
        
        for spread in spreads:
            print(f"     • {spread.name} ({spread.expiry}): "
                  f"{len(spread.legs)} legs, "
                  f"flow: ${spread.total_cash_flow:,.2f}")
        
        return True
    except Exception as e:
        print(f"  ❌ Spread grouping failed: {e}")
        return False


def test_pnl_calculation():
    """Test P&L calculations are deterministic."""
    print("\nTesting P&L calculations...")
    
    service = create_blotter_service(
        source="ib",
        host="127.0.0.1",
        port=4001,
        client_id=84,
    )
    
    try:
        blotter = service.build_blotter()
        
        # Verify math is consistent
        total_cash_flow = Decimal("0")
        total_commission = Decimal("0")
        
        for trade in blotter.trades:
            trade_flow = sum(e.net_cash_flow for e in trade.executions)
            trade_comm = sum(e.commission for e in trade.executions)
            
            # Verify trade totals match execution totals
            assert trade.total_cash_flow == trade_flow, \
                f"Cash flow mismatch for {trade.contract_desc}"
            assert trade.total_commission == trade_comm, \
                f"Commission mismatch for {trade.contract_desc}"
            
            total_cash_flow += trade_flow
            total_commission += trade_comm
        
        # Verify blotter totals
        assert blotter.total_commissions == total_commission, \
            "Blotter commission total mismatch"
        
        print(f"  ✅ P&L calculations verified")
        print(f"     Total cash flow: ${total_cash_flow:,.2f}")
        print(f"     Total commissions: ${total_commission:,.2f}")
        
        # Verify spread totals
        spreads = blotter.get_spreads()
        for spread in spreads:
            spread_flow = sum(leg.total_cash_flow for leg in spread.legs)
            assert spread.total_cash_flow == spread_flow, \
                f"Spread cash flow mismatch for {spread.name}"
        
        print(f"  ✅ Spread calculations verified")
        
        return True
    except AssertionError as e:
        print(f"  ❌ Calculation error: {e}")
        return False
    except Exception as e:
        print(f"  ❌ Test failed: {e}")
        return False


def run_all_tests():
    """Run all integration tests."""
    print("=" * 60)
    print("TRADE BLOTTER INTEGRATION TESTS")
    print("=" * 60)
    
    results = []
    
    results.append(("IB Connection", test_ib_connection()))
    
    if results[-1][1]:  # Only continue if connection works
        results.append(("Fetch Executions", test_fetch_today_executions()))
        results.append(("Spread Grouping", test_spread_grouping()))
        results.append(("P&L Calculation", test_pnl_calculation()))
    
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {name}")
    
    print("-" * 60)
    print(f"  {passed}/{total} tests passed")
    
    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
