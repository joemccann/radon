#!/usr/bin/env python3
"""
Interactive Brokers Order Execution with Auto-Monitor and Auto-Log

Places orders, monitors for fills, and automatically logs to trade_log.json.

This is the UNIFIED workflow script. Use this instead of separate
ib_order.py + ib_fill_monitor.py calls.

Requirements:
  pip install ib_insync

Usage:
  # Sell stock
  python3 scripts/ib_execute.py --type stock --symbol NFLX --qty 4500 --side SELL --limit 98.70

  # Buy option
  python3 scripts/ib_execute.py --type option --symbol GOOG --expiry 20260417 --strike 315 --right C --qty 10 --side BUY --limit 9.00

  # Use mid price
  python3 scripts/ib_execute.py --type option --symbol GOOG --expiry 20260417 --strike 315 --right C --qty 10 --side BUY --limit MID

  # Dry run
  python3 scripts/ib_execute.py --type stock --symbol NFLX --qty 100 --side SELL --limit 98.70 --dry-run

  # Skip confirmation prompt
  python3 scripts/ib_execute.py --type stock --symbol NFLX --qty 100 --side SELL --limit 98.70 --yes

  # Custom timeout for monitoring
  python3 scripts/ib_execute.py --type stock --symbol NFLX --qty 100 --side SELL --limit 98.70 --timeout 120
"""

import argparse
import json
import sys
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional, Dict, Any, List

try:
    from ib_insync import Stock, Option, LimitOrder, util
except ImportError:
    print("ERROR: ib_insync not installed")
    print("Install with: pip install ib_insync")
    sys.exit(1)

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Also add scripts dir so clients package is importable
sys.path.insert(0, str(Path(__file__).parent))

from clients.ib_client import IBClient, CLIENT_IDS, DEFAULT_HOST, DEFAULT_GATEWAY_PORT

DEFAULT_PORT = DEFAULT_GATEWAY_PORT
DEFAULT_CLIENT_ID = CLIENT_IDS.get("ib_execute", 25)
DEFAULT_TIMEOUT = 60  # 1 minute default monitor timeout
DEFAULT_INTERVAL = 3  # Check every 3 seconds

TRADE_LOG_PATH = PROJECT_ROOT / "data" / "trade_log.json"


class OrderExecutor:
    """Unified order executor with monitoring and logging"""

    def __init__(self, host: str, port: int, client_id: int):
        self.client = IBClient()
        self.host = host
        self.port = port
        self.client_id = client_id

    def connect(self) -> bool:
        try:
            self.client.connect(host=self.host, port=self.port, client_id=self.client_id)
            print(f"✓ Connected to IB on {self.host}:{self.port}")
            return True
        except Exception as e:
            print(f"✗ Connection failed: {e}")
            return False

    def disconnect(self):
        self.client.disconnect()
        print("✓ Disconnected from IB")

    def get_stock_contract(self, symbol: str) -> Stock:
        """Create and qualify a stock contract"""
        contract = Stock(symbol, 'SMART', 'USD')
        qualified = self.client.qualify_contracts(contract)
        if not qualified:
            raise ValueError(f"Could not qualify stock: {symbol}")
        return qualified[0]

    def get_option_contract(self, symbol: str, expiry: str, strike: float, right: str) -> Option:
        """Create and qualify an option contract"""
        contract = Option(
            symbol=symbol,
            lastTradeDateOrContractMonth=expiry,
            strike=strike,
            right=right,
            exchange='SMART',
            currency='USD'
        )
        qualified = self.client.qualify_contracts(contract)
        if not qualified:
            raise ValueError(f"Could not qualify option: {symbol} {expiry} ${strike} {right}")
        return qualified[0]

    def get_market_data(self, contract) -> Dict[str, float]:
        """Get current bid/ask/mid for contract"""
        ticker = self.client.get_quote(contract)

        # Wait for data
        for _ in range(50):
            self.client.sleep(0.1)
            if ticker.bid and ticker.ask and not util.isNan(ticker.bid) and not util.isNan(ticker.ask):
                break

        bid = ticker.bid if ticker.bid and not util.isNan(ticker.bid) else 0
        ask = ticker.ask if ticker.ask and not util.isNan(ticker.ask) else 0
        mid = (bid + ask) / 2 if bid and ask else 0
        last = ticker.last if ticker.last and not util.isNan(ticker.last) else mid

        self.client.cancel_market_data(contract)

        return {
            'bid': round(bid, 2),
            'ask': round(ask, 2),
            'mid': round(mid, 2),
            'last': round(last, 2),
            'spread': round(ask - bid, 2) if bid and ask else 0,
        }

    def place_order(self, contract, side: str, qty: int, limit_price: float) -> Optional[Any]:
        """Place a limit order and return trade object"""
        action = 'BUY' if side.upper() == 'BUY' else 'SELL'

        order = LimitOrder(
            action=action,
            totalQuantity=qty,
            lmtPrice=limit_price,
            tif='DAY',
            outsideRth=False
        )

        print(f"\n📤 Submitting order...")
        trade = self.client.place_order(contract, order)
        self.client.sleep(1)

        print(f"✓ Order submitted - ID: {trade.order.orderId}")
        return trade

    def monitor_order(self, trade, timeout: int = DEFAULT_TIMEOUT, interval: int = DEFAULT_INTERVAL) -> Dict[str, Any]:
        """
        Monitor an order for fills.

        Returns:
            dict with status, fills, total_qty, avg_price, total_value
        """
        order_id = trade.order.orderId
        symbol = trade.contract.symbol
        target_qty = int(trade.order.totalQuantity)

        print(f"\n📡 Monitoring order #{order_id} for fills...")
        print(f"   Symbol: {symbol}")
        print(f"   Quantity: {target_qty}")
        print(f"   Timeout: {timeout}s")
        print("=" * 50)

        fills = []
        total_filled = 0
        start_time = datetime.now()

        checks = 0
        max_checks = timeout // interval

        while checks < max_checks:
            self.client.sleep(interval)
            checks += 1

            # Get current order status
            trades = self.client.get_open_orders()
            self.client.sleep(0.5)
            
            # Check order status from trade object
            status = trade.orderStatus
            current_filled = int(status.filled)
            
            timestamp = datetime.now().strftime('%H:%M:%S')
            
            if status.status == 'Filled':
                print(f"\n✅ ORDER FILLED!")
                print(f"   Filled: {current_filled}")
                print(f"   Avg Price: ${status.avgFillPrice:.2f}")
                
                return {
                    'status': 'filled',
                    'order_id': order_id,
                    'symbol': symbol,
                    'side': trade.order.action,
                    'quantity': current_filled,
                    'avg_price': status.avgFillPrice,
                    'total_value': current_filled * status.avgFillPrice,
                    'commission': 0,  # Will be updated from fills
                }
            
            elif current_filled > total_filled:
                # Partial fill
                new_fills = current_filled - total_filled
                total_filled = current_filled
                print(f"[{timestamp}] Partial fill: {current_filled}/{target_qty} @ ${status.avgFillPrice:.2f}")
            
            elif status.status in ['Cancelled', 'ApiCancelled']:
                print(f"\n⚠️ Order cancelled")
                return {
                    'status': 'cancelled',
                    'order_id': order_id,
                    'symbol': symbol,
                    'quantity': current_filled,
                }
            
            else:
                # Still working
                print(f"[{timestamp}] Working... {status.status}")
            
            # Check if order disappeared (filled outside our view)
            open_orders = self.client.get_open_trades()
            order_still_open = any(t.order.orderId == order_id for t in open_orders)
            
            if not order_still_open:
                # Order is no longer open - check executions
                executions = self.client.get_fills()
                order_fills = [f for f in executions if f.contract.symbol == symbol]
                
                if order_fills:
                    total_qty = sum(int(f.execution.shares) for f in order_fills)
                    total_value = sum(f.execution.shares * f.execution.avgPrice for f in order_fills)
                    avg_price = total_value / total_qty if total_qty > 0 else 0
                    total_commission = sum(f.commissionReport.commission for f in order_fills if f.commissionReport)
                    
                    print(f"\n✅ ORDER FILLED (confirmed via executions)")
                    print(f"   Total Qty: {total_qty}")
                    print(f"   Avg Price: ${avg_price:.2f}")
                    print(f"   Total Value: ${total_value:,.2f}")
                    if total_commission:
                        print(f"   Commission: ${total_commission:.2f}")
                    
                    return {
                        'status': 'filled',
                        'order_id': order_id,
                        'symbol': symbol,
                        'side': trade.order.action,
                        'quantity': int(total_qty),
                        'avg_price': round(avg_price, 2),
                        'total_value': round(total_value, 2),
                        'commission': round(total_commission, 2) if total_commission else 0,
                        'fills': [{
                            'qty': int(f.execution.shares),
                            'price': f.execution.avgPrice,
                            'time': str(f.execution.time)
                        } for f in order_fills]
                    }
                
                print(f"\n⚠️ Order no longer open but no fills found")
                return {
                    'status': 'unknown',
                    'order_id': order_id,
                    'symbol': symbol,
                }
        
        # Timeout
        print(f"\n⏳ Timeout after {timeout}s - order still working")
        return {
            'status': 'timeout',
            'order_id': order_id,
            'symbol': symbol,
            'filled_so_far': total_filled,
        }
    
    def log_trade(self, result: Dict[str, Any], contract, side: str, limit_price: float, 
                  thesis: str = "", notes: str = "") -> bool:
        """
        Log a filled trade to trade_log.json
        
        Returns True if successfully logged.
        """
        if result.get('status') != 'filled':
            print(f"⚠️ Cannot log - order not filled (status: {result.get('status')})")
            return False
        
        # Load existing trade log
        if TRADE_LOG_PATH.exists():
            with open(TRADE_LOG_PATH) as f:
                trade_log = json.load(f)
        else:
            trade_log = {"trades": []}
        
        # Get next ID
        existing_ids = [t.get('id', 0) for t in trade_log.get('trades', [])]
        next_id = max(existing_ids, default=0) + 1
        
        # Determine contract type and structure
        if hasattr(contract, 'lastTradeDateOrContractMonth'):
            # Option
            contract_type = 'option'
            contract_str = f"{contract.symbol} {contract.lastTradeDateOrContractMonth} ${contract.strike} {'Call' if contract.right == 'C' else 'Put'}"
            structure = f"Long {'Call' if contract.right == 'C' else 'Put'}" if side == 'BUY' else f"Short {'Call' if contract.right == 'C' else 'Put'}"
            multiplier = 100
        else:
            # Stock
            contract_type = 'stock'
            contract_str = contract.symbol
            structure = "Long Stock" if side == 'BUY' else "Sold Stock"
            multiplier = 1
        
        # Create trade entry
        now = datetime.now()
        trade_entry = {
            "id": next_id,
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "ticker": result['symbol'],
            "contract_type": contract_type,
            "contract": contract_str,
            "structure": structure,
            "action": side,
            "decision": "EXECUTED",
            "order_id": result['order_id'],
            "quantity": result['quantity'],
            "fill_price": result['avg_price'],
            "total_value": round(result['total_value'], 2),
            "commission": result.get('commission', 0),
            "limit_price": limit_price,
            "thesis": thesis,
            "notes": notes,
            "fills": result.get('fills', []),
        }
        
        # Add to log
        trade_log['trades'].append(trade_entry)
        
        # Save
        with open(TRADE_LOG_PATH, 'w') as f:
            json.dump(trade_log, f, indent=2)

        # Phase 3 dual-write — best-effort.
        try:
            import sys as _sys
            _sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
            from db.writer import upsert_journal_entry
            trade_id = f"{trade_entry['date']}T{trade_entry['time']}#{next_id}"
            upsert_journal_entry(trade_id, trade_entry, filled_at=trade_entry.get("date"))
        except Exception as exc:  # noqa: BLE001
            print(f"  Warning: journal db dual-write failed: {exc}")

        print(f"\n📝 Logged to trade_log.json (ID: {next_id})")
        return True


def main():
    parser = argparse.ArgumentParser(description="Execute IB orders with auto-monitoring and logging")
    
    # Order type
    parser.add_argument("--type", required=True, choices=['stock', 'option'], help="Order type")
    
    # Common parameters
    parser.add_argument("--symbol", required=True, help="Symbol (e.g., NFLX, GOOG)")
    parser.add_argument("--qty", type=int, required=True, help="Quantity (shares or contracts)")
    parser.add_argument("--side", required=True, choices=['BUY', 'SELL'], help="BUY or SELL")
    parser.add_argument("--limit", required=True, help="Limit price or 'MID' or 'BID' or 'ASK'")
    
    # Option-specific parameters
    parser.add_argument("--expiry", help="Expiry date YYYYMMDD (required for options)")
    parser.add_argument("--strike", type=float, help="Strike price (required for options)")
    parser.add_argument("--right", choices=['C', 'P'], help="C=Call, P=Put (required for options)")
    
    # Connection
    parser.add_argument("--host", default=DEFAULT_HOST, help="TWS/Gateway host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port")
    parser.add_argument("--client-id", type=int, default=DEFAULT_CLIENT_ID, help="Client ID")
    
    # Monitoring
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Monitor timeout in seconds")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="Check interval in seconds")
    
    # Behavior
    parser.add_argument("--dry-run", action="store_true", help="Preview without submitting")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--no-log", action="store_true", help="Don't log to trade_log.json")
    
    # Logging metadata
    parser.add_argument("--thesis", default="", help="Trade thesis for logging")
    parser.add_argument("--notes", default="", help="Additional notes for logging")
    
    args = parser.parse_args()
    
    # Validate option parameters
    if args.type == 'option':
        if not all([args.expiry, args.strike, args.right]):
            parser.error("Options require --expiry, --strike, and --right")
    
    # Create executor
    executor = OrderExecutor(args.host, args.port, args.client_id)
    
    if not executor.connect():
        sys.exit(1)
    
    try:
        # Get contract
        if args.type == 'stock':
            contract = executor.get_stock_contract(args.symbol)
            print(f"\n📋 Contract: {args.symbol} (Stock)")
        else:
            contract = executor.get_option_contract(args.symbol, args.expiry, args.strike, args.right)
            print(f"\n📋 Contract: {contract.localSymbol}")
        
        # Get market data
        print(f"\n💹 Fetching market data...")
        mkt = executor.get_market_data(contract)
        print(f"   Bid: ${mkt['bid']:.2f}")
        print(f"   Ask: ${mkt['ask']:.2f}")
        print(f"   Mid: ${mkt['mid']:.2f}")
        if mkt['spread']:
            print(f"   Spread: ${mkt['spread']:.2f}")
        
        # Determine limit price
        limit_str = args.limit.upper()
        if limit_str == 'MID':
            limit_price = mkt['mid']
        elif limit_str == 'BID':
            limit_price = mkt['bid']
        elif limit_str == 'ASK':
            limit_price = mkt['ask']
        else:
            limit_price = float(args.limit)
        
        if not limit_price or limit_price <= 0:
            print(f"✗ Invalid limit price: {limit_price}")
            sys.exit(1)
        
        # Calculate total value
        multiplier = 100 if args.type == 'option' else 1
        total_value = limit_price * args.qty * multiplier
        
        # Show order summary
        print(f"\n💰 Order Summary:")
        print(f"   {args.side} {args.qty}x {args.symbol}")
        print(f"   @ ${limit_price:.2f}")
        print(f"   Total: ${total_value:,.2f}")
        
        # Dry run exit
        if args.dry_run:
            print(f"\n🔍 DRY RUN - Order NOT submitted")
            sys.exit(0)
        
        # Confirm
        if not args.yes:
            print(f"\n" + "=" * 50)
            confirm = input("⚠️  CONFIRM ORDER? (type 'YES' to proceed): ")
            if confirm != 'YES':
                print("Order cancelled.")
                sys.exit(0)
        
        # Place order
        trade = executor.place_order(contract, args.side, args.qty, limit_price)
        
        if not trade:
            print("✗ Failed to place order")
            sys.exit(1)
        
        # Monitor for fills
        result = executor.monitor_order(trade, timeout=args.timeout, interval=args.interval)
        
        # Log if filled
        if result['status'] == 'filled' and not args.no_log:
            executor.log_trade(
                result, 
                contract, 
                args.side, 
                limit_price,
                thesis=args.thesis,
                notes=args.notes
            )
        
        # Final summary
        print(f"\n{'=' * 50}")
        if result['status'] == 'filled':
            print(f"✅ COMPLETE")
            print(f"   Symbol: {result['symbol']}")
            print(f"   Quantity: {result['quantity']}")
            print(f"   Avg Price: ${result['avg_price']:.2f}")
            print(f"   Total Value: ${result['total_value']:,.2f}")
            if result.get('commission'):
                print(f"   Commission: ${result['commission']:.2f}")
            sys.exit(0)
        elif result['status'] == 'timeout':
            print(f"⏳ Order still working after {args.timeout}s")
            print(f"   Order ID: {result['order_id']}")
            print(f"   Check TWS for status")
            sys.exit(2)
        else:
            print(f"⚠️ Order status: {result['status']}")
            sys.exit(1)
    
    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)
    
    finally:
        executor.disconnect()


if __name__ == "__main__":
    main()
