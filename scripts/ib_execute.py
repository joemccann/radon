#!/usr/bin/env python3
"""
Interactive Brokers Order Execution with Auto-Monitor and Auto-Log

Places orders, monitors for fills, and automatically logs to Turso journal.

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
import sys
from datetime import datetime
from decimal import Decimal
from enum import Enum
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
from utils.ib_order_status import API_CANCELLED, CANCELLED, FILLED_STATUS, TERMINAL_FAILED

DEFAULT_PORT = DEFAULT_GATEWAY_PORT
DEFAULT_CLIENT_ID = CLIENT_IDS.get("ib_execute", 25)
DEFAULT_TIMEOUT = 60  # 1 minute default monitor timeout
DEFAULT_INTERVAL = 3  # Check every 3 seconds
EXIT_FILLED_NOT_JOURNALED = 4


class MonitorOutcome(str, Enum):
    """How a monitored order ended; the value is the result's ``status``."""

    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"  # ended (or vanished) with some, not all, filled
    CANCELLED = "cancelled"                # Cancelled / ApiCancelled, nothing filled
    REJECTED = "rejected"                  # Inactive / Rejected, nothing filled
    UNKNOWN = "unknown"                    # left the open list with no status and no fills
    TIMEOUT = "timeout"                    # still working at the deadline


def classify_monitor(status: str, *, is_open: bool, filled: float, target: float) -> Optional[MonitorOutcome]:
    """The single transition out of "working": None while the order still works.

    Absence from the open list covers Filled, Cancelled, ApiCancelled and
    Inactive alike, so a vanished order is named only from its own fills.
    """
    if status == FILLED_STATUS:
        return MonitorOutcome.FILLED
    if status in TERMINAL_FAILED:
        if filled > 0:
            return MonitorOutcome.PARTIALLY_FILLED
        if status in (CANCELLED, API_CANCELLED):
            return MonitorOutcome.CANCELLED
        return MonitorOutcome.REJECTED
    if is_open:
        return None
    if target > 0 and filled >= target:
        return MonitorOutcome.FILLED
    if filled > 0:
        return MonitorOutcome.PARTIALLY_FILLED
    return MonitorOutcome.UNKNOWN


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

    def _order_fills(self, trade) -> List[Any]:
        """This order's executions, matched by permId (orderId + clientId when
        IB has not assigned one) and deduplicated by execId. Never by symbol:
        that summed every same-day fill in the name."""
        order = trade.order
        by_exec_id: Dict[Any, Any] = {}
        for fill in [*self.client.get_fills(), *(getattr(trade, "fills", None) or [])]:
            execution = fill.execution
            if order.permId:
                if execution.permId != order.permId:
                    continue
            elif execution.orderId != order.orderId or execution.clientId != order.clientId:
                continue
            by_exec_id.setdefault(execution.execId, fill)
        return list(by_exec_id.values())

    def monitor_order(self, trade, timeout: int = DEFAULT_TIMEOUT, interval: int = DEFAULT_INTERVAL) -> Dict[str, Any]:
        """
        Monitor an order until classify_monitor names an outcome or timeout.

        Returns:
            dict with status (a MonitorOutcome value), broker_status, and for
            any execution: quantity, avg_price, total_value, commission, fills
        """
        order_id = trade.order.orderId
        symbol = trade.contract.symbol
        target_qty = int(trade.order.totalQuantity)

        print(f"\n📡 Monitoring order #{order_id} for fills...")
        print(f"   Symbol: {symbol}")
        print(f"   Quantity: {target_qty}")
        print(f"   Timeout: {timeout}s")
        print("=" * 50)

        total_filled = 0
        checks = 0
        max_checks = timeout // interval

        while checks < max_checks:
            self.client.sleep(interval)
            checks += 1

            # Refresh open orders so the trade object carries the latest status
            self.client.get_open_orders()
            self.client.sleep(0.5)

            status = trade.orderStatus
            broker_status = status.status
            is_open = any(t.order.orderId == order_id for t in self.client.get_open_trades())
            order_fills = self._order_fills(trade)
            filled = max(
                float(status.filled or 0),
                float(sum(f.execution.shares for f in order_fills)),
            )
            outcome = classify_monitor(broker_status, is_open=is_open, filled=filled, target=target_qty)

            if outcome is None:
                timestamp = datetime.now().strftime('%H:%M:%S')
                if filled > total_filled:
                    total_filled = filled
                    print(f"[{timestamp}] Partial fill: {filled:g}/{target_qty} @ ${status.avgFillPrice:.2f}")
                else:
                    print(f"[{timestamp}] Working... {broker_status}")
                continue

            result: Dict[str, Any] = {
                'status': outcome.value,
                'broker_status': broker_status,
                'order_id': order_id,
                'symbol': symbol,
                'side': trade.order.action,
                'quantity': int(filled),
                'avg_price': status.avgFillPrice,
                'total_value': filled * status.avgFillPrice,
                'commission': 0,
            }
            if order_fills:
                total_qty = sum(f.execution.shares for f in order_fills)
                total_value = sum(f.execution.shares * f.execution.avgPrice for f in order_fills)
                total_commission = sum(
                    f.commissionReport.commission for f in order_fills if f.commissionReport
                )
                result.update({
                    'quantity': int(total_qty),
                    'avg_price': round(total_value / total_qty, 2) if total_qty else 0,
                    'total_value': round(total_value, 2),
                    'commission': round(total_commission, 2) if total_commission else 0,
                    'fills': [{
                        'qty': int(f.execution.shares),
                        'price': f.execution.avgPrice,
                        'time': str(f.execution.time),
                    } for f in order_fills],
                })

            if outcome is MonitorOutcome.FILLED:
                print(f"\n✅ ORDER FILLED")
                print(f"   Filled: {result['quantity']}")
                print(f"   Avg Price: ${result['avg_price']:.2f}")
            elif outcome is MonitorOutcome.PARTIALLY_FILLED:
                print(f"\n⚠️ Order ended {broker_status} after a partial fill of {result['quantity']}/{target_qty}")
            elif outcome is MonitorOutcome.UNKNOWN:
                print(f"\n⚠️ Order no longer open but no fills found")
            else:
                print(f"\n⚠️ Order {broker_status}")
            return result

        # Timeout
        print(f"\n⏳ Timeout after {timeout}s - order still working")
        return {
            'status': MonitorOutcome.TIMEOUT.value,
            'order_id': order_id,
            'symbol': symbol,
            'filled_so_far': total_filled,
        }

    def log_trade(self, result: Dict[str, Any], contract, side: str, limit_price: float, 
                  thesis: str = "", notes: str = "") -> bool:
        """
        Log a filled trade to the canonical Turso journal.
        
        Returns True if successfully logged.
        """
        if result.get('status') != 'filled':
            print(f"⚠️ Cannot log - order not filled (status: {result.get('status')})")
            return False

        try:
            from db.readers import read_next_journal_numeric_id
            next_id = read_next_journal_numeric_id()
        except Exception as exc:  # noqa: BLE001
            print(f"  Error: journal id read failed (refusing next_id=1 fallback): {exc}")
            return False
        
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
        
        try:
            from db.writer import upsert_journal_entry
            trade_id = f"{trade_entry['date']}T{trade_entry['time']}#{next_id}"
            upsert_journal_entry(trade_id, trade_entry, filled_at=trade_entry.get("date"))
        except Exception as exc:  # noqa: BLE001
            print(f"  Error: journal db write failed: {exc}")
            return False

        print(f"\n📝 Logged to Turso journal (ID: {next_id})")
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
    parser.add_argument("--no-log", action="store_true", help="Don't log to Turso journal")
    
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
        
        # Kill switch (REL-004): the --yes flag must not bypass a halt.
        from trading_halt import is_trading_halted, get_halt_state
        if is_trading_halted():
            print(f"✗ TRADING HALTED — order not placed "
                  f"({get_halt_state().get('reason', 'manual halt')})")
            sys.exit(3)

        # Server-side fat-finger bounds (REL-005), the same caps every other
        # placer funnels through (ib_place_order.py, api/server.py place and
        # modify). This file honoured the halt but not the caps, and
        # risk_reversal.py renders a `--yes` invocation of it into the
        # operator's own report. R-250.
        from order_limits import check_order_limits
        violation = check_order_limits({
            "type": args.type,
            "symbol": args.symbol,
            "action": args.side,
            "quantity": args.qty,
            "limitPrice": limit_price,
        })
        if violation:
            print(f"✗ ORDER REFUSED — {violation['message']}")
            sys.exit(4)

        # Gate 3 (NF-1), when RADON_BANKROLL_CAP_ENFORCE_ALL_PATHS is On.
        from bankroll_guard import check_if_enforced_on_all_paths
        bankroll_violation = check_if_enforced_on_all_paths({
            "type": args.type,
            "symbol": args.symbol,
            "action": args.side,
            "quantity": args.qty,
            "limitPrice": limit_price,
            "expiry": args.expiry,
            "strike": args.strike,
            "right": args.right,
        })
        if bankroll_violation:
            print(f"✗ ORDER REFUSED — {bankroll_violation['message']}")
            sys.exit(4)

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
        journaled = True
        if result['status'] == 'filled' and not args.no_log:
            journaled = executor.log_trade(
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
            if journaled:
                print(f"✅ COMPLETE")
            else:
                print("🚨" * 25)
                print("🚨 FILLED BUT NOT JOURNALED")
                print("🚨 The order filled at IB but the journal write failed.")
                print("🚨 Record this fill manually or run journal_sync now.")
                print("🚨" * 25)
            print(f"   Symbol: {result['symbol']}")
            print(f"   Quantity: {result['quantity']}")
            print(f"   Avg Price: ${result['avg_price']:.2f}")
            print(f"   Total Value: ${result['total_value']:,.2f}")
            if result.get('commission'):
                print(f"   Commission: ${result['commission']:.2f}")
            sys.exit(0 if journaled else EXIT_FILLED_NOT_JOURNALED)
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
