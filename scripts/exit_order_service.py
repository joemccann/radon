#!/usr/bin/env python3
"""
Exit Order Service

Monitors positions with pending manual exit orders and places them
when the spread price reaches a level IB will accept.

Runs:
  - At Pi startup
  - Every 5 minutes during market hours (9:30 AM - 4:00 PM ET)

Usage:
  python3 scripts/exit_order_service.py              # Check and place orders
  python3 scripts/exit_order_service.py --daemon     # Run continuously during market hours
  python3 scripts/exit_order_service.py --status     # Show pending orders status
  python3 scripts/exit_order_service.py --dry-run    # Preview without placing orders
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
import pytz

try:
    from ib_insync import Option, ComboLeg, Contract, LimitOrder, util
except ImportError:
    print("ERROR: ib_insync not installed")
    print("Install with: pip install ib_insync")
    sys.exit(1)

from clients.ib_client import IBClient, CLIENT_IDS, DEFAULT_HOST, DEFAULT_GATEWAY_PORT

# Configuration
DEFAULT_PORT = DEFAULT_GATEWAY_PORT
DEFAULT_CLIENT_ID = CLIENT_IDS["exit_order_service"]
CHECK_INTERVAL = 300  # 5 minutes in seconds

# Paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
SERVICE_LOG_PATH = PROJECT_DIR / "data" / "exit_order_service.log"

# IB will accept limit orders within this percentage of current price
# e.g., 0.5 means we can place orders up to 50% above current price
IB_LIMIT_THRESHOLD = 0.40  # 40% above current - conservative estimate


def log(message: str, level: str = "INFO"):
    """Log message to console and file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] [{level}] {message}"
    print(log_line)
    
    try:
        with open(SERVICE_LOG_PATH, "a") as f:
            f.write(log_line + "\n")
    except Exception:
        pass


def is_market_open() -> bool:
    """Check if US market is currently open"""
    et = pytz.timezone('America/New_York')
    now = datetime.now(et)
    
    # Check if weekday (Monday=0, Friday=4)
    if now.weekday() > 4:
        return False
    
    # Check time: 9:30 AM - 4:00 PM ET
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    
    return market_open <= now <= market_close


def get_next_market_open() -> datetime:
    """Get the next market open time"""
    et = pytz.timezone('America/New_York')
    now = datetime.now(et)
    
    # Start with today at 9:30 AM
    next_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    
    # If we're past today's open, move to tomorrow
    if now >= next_open:
        next_open += timedelta(days=1)
    
    # Skip weekends
    while next_open.weekday() > 4:
        next_open += timedelta(days=1)
    
    return next_open


def load_pending_exits() -> List[Dict[str, Any]]:
    """Load trades with pending manual exit orders from the Turso journal."""
    try:
        from db.client import get_db

        rows = get_db().execute(
            """
            SELECT trade_id, payload
            FROM journal
            ORDER BY COALESCE(filled_at, written_at) DESC
            """
        ).fetchall()
    except Exception as e:
        log(f"Failed to load journal exit orders: {e}", "ERROR")
        return []

    pending = []
    for row in rows:
        journal_trade_id = row[0] if isinstance(row, (tuple, list)) else getattr(row, "trade_id", None)
        raw_payload = row[1] if isinstance(row, (tuple, list)) else getattr(row, "payload", None)
        try:
            trade = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(trade, dict):
            continue

        exit_orders = trade.get("exit_orders", {})
        target = exit_orders.get("target", {})
        
        # Check if this has a pending manual target order
        if target.get("status") == "PENDING_MANUAL":
            pending.append({
                "trade_id": trade.get("id"),
                "ticker": trade.get("ticker"),
                "structure": trade.get("structure"),
                "contract": trade.get("contract"),
                "contracts": trade.get("contracts"),
                "entry_price": trade.get("fill_price"),
                "target_price": target.get("target_price"),
                "legs": trade.get("legs", []),
                "journal_trade_id": journal_trade_id,
            })
    
    return pending


def update_trade_log(trade_id: int, order_id: int, status: str) -> bool:
    """Update the Turso journal with new exit order status."""
    try:
        from db.client import get_db

        db = get_db()
        rows = db.execute("SELECT trade_id, payload FROM journal").fetchall()

        target_journal_id = None
        target_trade = None
        for row in rows:
            journal_trade_id = row[0] if isinstance(row, (tuple, list)) else getattr(row, "trade_id", None)
            raw_payload = row[1] if isinstance(row, (tuple, list)) else getattr(row, "payload", None)
            try:
                trade = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(trade, dict):
                continue
            if trade.get("id") == trade_id:
                target_journal_id = journal_trade_id
                target_trade = trade
                break

        if target_journal_id is None or target_trade is None:
            raise RuntimeError(f"journal trade not found for id {trade_id}")

        if "exit_orders" not in target_trade:
            target_trade["exit_orders"] = {}

        target_trade["exit_orders"]["target"] = {
            "order_id": order_id,
            "target_price": target_trade["exit_orders"].get("target", {}).get("target_price"),
            "status": status,
            "placed": datetime.now().isoformat(),
        }

        db.execute(
            """
            UPDATE journal
            SET payload = ?, written_at = ?
            WHERE trade_id = ?
            """,
            (json.dumps(target_trade), datetime.now().isoformat(), target_journal_id),
        )
        if hasattr(db, "commit"):
            db.commit()

        return True
    except Exception as e:
        log(f"Failed to update journal trade: {e}", "ERROR")
        return False


def extract_expiry(order: Dict[str, Any]) -> Optional[str]:
    """Extract expiry date from order data, returning YYYYMMDD string or None.

    Checks (in order):
      1. ``expiry`` field on any leg in ``order["legs"]``
      2. Contract description string (e.g. "GOOG Apr 17, 2026 ...")

    Returns None if no expiry can be determined.
    """
    MONTH_MAP = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4,
        "may": 5, "jun": 6, "jul": 7, "aug": 8,
        "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    # --- 1. Try legs ---
    legs = order.get("legs", [])
    for leg in legs:
        raw = leg.get("expiry")
        if raw:
            # Accept YYYY-MM-DD or YYYYMMDD
            cleaned = str(raw).replace("-", "")
            if len(cleaned) == 8 and cleaned.isdigit():
                return cleaned

    # --- 2. Try contract description ---
    contract = order.get("contract", "")
    if contract:
        # Pattern: "Mon DD, YYYY" e.g. "Apr 17, 2026"
        m = re.search(
            r"\b([A-Za-z]{3})\s+(\d{1,2}),?\s+(\d{4})\b", contract
        )
        if m:
            month_str, day_str, year_str = m.group(1), m.group(2), m.group(3)
            month_num = MONTH_MAP.get(month_str.lower())
            if month_num:
                return f"{year_str}{month_num:02d}{int(day_str):02d}"

    return None


def get_spread_price(client: IBClient, ticker: str, legs: List[Dict]) -> Optional[float]:
    """Get current mid price for a spread"""
    if not legs or len(legs) < 2:
        return None
    
    # Parse leg info
    long_leg = None
    short_leg = None
    
    for leg in legs:
        if leg.get("type") == "Long Call":
            long_leg = leg
        elif leg.get("type") == "Short Call":
            short_leg = leg
    
    if not long_leg or not short_leg:
        return None
    
    # Extract expiry from legs or contract description
    expiry = extract_expiry({"legs": legs})
    if expiry is None:
        expiry = "20260417"  # Last-resort fallback
        log("WARNING: Could not extract expiry from order data, using hardcoded fallback 20260417", "WARNING")

    try:
        # Create and qualify contracts
        long_call = Option(
            ticker, expiry, long_leg["strike"], 'C', 'SMART', currency='USD'
        )
        short_call = Option(
            ticker, expiry, short_leg["strike"], 'C', 'SMART', currency='USD'
        )
        
        qualified = client.qualify_contracts(long_call, short_call)
        if len(qualified) != 2:
            return None

        long_call, short_call = qualified

        # Get market data
        long_ticker = client.get_quote(long_call)
        short_ticker = client.get_quote(short_call)
        client.sleep(2)

        # Calculate mid prices
        long_bid = long_ticker.bid if long_ticker.bid and not util.isNan(long_ticker.bid) else 0
        long_ask = long_ticker.ask if long_ticker.ask and not util.isNan(long_ticker.ask) else 0
        short_bid = short_ticker.bid if short_ticker.bid and not util.isNan(short_ticker.bid) else 0
        short_ask = short_ticker.ask if short_ticker.ask and not util.isNan(short_ticker.ask) else 0

        client.cancel_market_data(long_call)
        client.cancel_market_data(short_call)
        
        if not all([long_bid, long_ask, short_bid, short_ask]):
            return None
        
        long_mid = (long_bid + long_ask) / 2
        short_mid = (short_bid + short_ask) / 2
        spread_mid = long_mid - short_mid
        
        return round(spread_mid, 2)
    
    except Exception as e:
        log(f"Error getting spread price: {e}", "ERROR")
        return None


def can_place_order(current_price: float, target_price: float) -> bool:
    """Check if IB will likely accept the order at this price"""
    if current_price <= 0:
        return False
    
    # Calculate how far the target is from current
    gap_ratio = (target_price - current_price) / current_price
    
    # IB typically accepts orders within ~40-50% of current price
    return gap_ratio <= IB_LIMIT_THRESHOLD


def place_target_order(
    client: IBClient,
    ticker: str,
    legs: List[Dict],
    contracts: int,
    target_price: float,
    dry_run: bool = False,
    order_data: Optional[Dict] = None
) -> Optional[int]:
    """Place a target exit order for a spread"""

    if not legs or len(legs) < 2:
        log("Invalid legs configuration", "ERROR")
        return None

    # Parse legs
    long_leg = None
    short_leg = None
    for leg in legs:
        if leg.get("type") == "Long Call":
            long_leg = leg
        elif leg.get("type") == "Short Call":
            short_leg = leg

    if not long_leg or not short_leg:
        log("Could not identify long/short legs", "ERROR")
        return None

    # Extract expiry from order data or legs
    expiry = extract_expiry(order_data or {"legs": legs})
    if expiry is None:
        expiry = "20260417"  # Last-resort fallback
        log("WARNING: Could not extract expiry from order data, using hardcoded fallback 20260417", "WARNING")
    
    try:
        # Qualify contracts
        long_call = Option(ticker, expiry, long_leg["strike"], 'C', 'SMART', currency='USD')
        short_call = Option(ticker, expiry, short_leg["strike"], 'C', 'SMART', currency='USD')
        client.qualify_contracts(long_call, short_call)

        # Create combo for closing (SELL the spread)
        combo = Contract()
        combo.symbol = ticker
        combo.secType = 'BAG'
        combo.currency = 'USD'
        combo.exchange = 'SMART'
        
        leg1 = ComboLeg()
        leg1.conId = long_call.conId
        leg1.ratio = 1
        leg1.action = 'SELL'  # Sell to close long
        leg1.exchange = 'SMART'
        
        leg2 = ComboLeg()
        leg2.conId = short_call.conId
        leg2.ratio = 1
        leg2.action = 'BUY'   # Buy to close short
        leg2.exchange = 'SMART'
        
        combo.comboLegs = [leg1, leg2]
        
        # Create limit order
        order = LimitOrder(
            action='SELL',
            totalQuantity=contracts,
            lmtPrice=target_price,
            tif='GTC',
            outsideRth=False
        )
        order.orderRef = f"{ticker}_TARGET_EXIT"
        
        if dry_run:
            log(f"DRY RUN: Would place SELL {contracts}x {ticker} spread @ ${target_price:.2f}")
            return -1
        
        # Place order
        trade = client.place_order(combo, order)
        client.sleep(3)
        
        if trade.orderStatus.status in ['Submitted', 'PreSubmitted']:
            log(f"✓ Target order placed: #{trade.order.orderId} @ ${target_price:.2f}")
            return trade.order.orderId
        elif trade.orderStatus.status in ['Cancelled', 'Inactive']:
            log(f"✗ Order rejected: {trade.orderStatus.status}", "WARNING")
            return None
        else:
            log(f"Order status: {trade.orderStatus.status}")
            return trade.order.orderId
    
    except Exception as e:
        log(f"Error placing order: {e}", "ERROR")
        return None


def check_and_place_orders(dry_run: bool = False) -> Dict[str, Any]:
    """Main function to check pending orders and place when possible"""
    
    result = {
        "timestamp": datetime.now().isoformat(),
        "market_open": is_market_open(),
        "pending_orders": [],
        "placed_orders": [],
        "errors": []
    }
    
    if not is_market_open():
        next_open = get_next_market_open()
        log(f"Market closed. Next open: {next_open.strftime('%Y-%m-%d %H:%M %Z')}")
        result["next_check"] = next_open.isoformat()
        return result
    
    # Load pending exits
    pending = load_pending_exits()
    if not pending:
        log("No pending manual exit orders found")
        return result
    
    log(f"Found {len(pending)} pending exit order(s)")
    result["pending_orders"] = pending
    
    # Connect to IB
    client = IBClient()
    try:
        client.connect(host=DEFAULT_HOST, port=DEFAULT_PORT, client_id=DEFAULT_CLIENT_ID)
        log("Connected to IB Gateway")
    except Exception as e:
        log(f"Failed to connect to IB: {e}", "ERROR")
        result["errors"].append(str(e))
        return result

    try:
        for order_info in pending:
            ticker = order_info["ticker"]
            target_price = order_info["target_price"]
            contracts = order_info["contracts"]
            legs = order_info["legs"]
            trade_id = order_info["trade_id"]

            log(f"\nChecking {ticker}...")

            # Get current spread price
            current_price = get_spread_price(client, ticker, legs)
            if current_price is None:
                log(f"Could not get current price for {ticker}", "WARNING")
                continue
            
            log(f"  Current: ${current_price:.2f} | Target: ${target_price:.2f}")
            
            # Check if we can place the order
            if can_place_order(current_price, target_price):
                log(f"  ✓ Within IB limit threshold - attempting to place order")
                
                order_id = place_target_order(
                    client, ticker, legs, contracts, target_price, dry_run,
                    order_data=order_info
                )
                
                if order_id:
                    result["placed_orders"].append({
                        "ticker": ticker,
                        "order_id": order_id,
                        "target_price": target_price
                    })
                    
                    if not dry_run:
                        update_trade_log(trade_id, order_id, "ACTIVE")
            else:
                gap = target_price - current_price
                gap_pct = (gap / current_price) * 100
                threshold_price = current_price * (1 + IB_LIMIT_THRESHOLD)
                
                log(f"  ✗ Target too far from current ({gap_pct:.1f}% gap)")
                log(f"  → IB will accept when spread reaches ~${threshold_price:.2f}")
    
    finally:
        client.disconnect()
        log("Disconnected from IB")

    return result


def show_status():
    """Show status of all pending exit orders"""
    pending = load_pending_exits()
    
    print("=" * 60)
    print("PENDING MANUAL EXIT ORDERS")
    print("=" * 60)
    
    if not pending:
        print("\nNo pending manual exit orders found.")
        return
    
    for order in pending:
        print(f"\nTrade #{order['trade_id']}: {order['ticker']}")
        print(f"  Structure: {order['structure']}")
        print(f"  Contracts: {order['contracts']}")
        print(f"  Entry: ${order['entry_price']:.2f}")
        print(f"  Target: ${order['target_price']:.2f}")
        print(f"  Required move: +{((order['target_price']/order['entry_price'])-1)*100:.1f}%")
    
    print(f"\n{'=' * 60}")
    print(f"Market Status: {'OPEN' if is_market_open() else 'CLOSED'}")
    if not is_market_open():
        next_open = get_next_market_open()
        print(f"Next Open: {next_open.strftime('%Y-%m-%d %H:%M %Z')}")


def run_daemon():
    """Run as a daemon, checking every 5 minutes during market hours"""
    log("Starting exit order service daemon...")
    log(f"Check interval: {CHECK_INTERVAL} seconds")
    
    while True:
        try:
            if is_market_open():
                log("\n" + "=" * 40)
                check_and_place_orders()
                log(f"Next check in {CHECK_INTERVAL} seconds...")
            else:
                next_open = get_next_market_open()
                # Sleep until market opens
                sleep_seconds = (next_open - datetime.now(pytz.timezone('America/New_York'))).total_seconds()
                if sleep_seconds > 0:
                    log(f"Market closed. Sleeping until {next_open.strftime('%H:%M %Z')}")
                    time.sleep(min(sleep_seconds, 3600))  # Max 1 hour sleep
                    continue
            
            time.sleep(CHECK_INTERVAL)
        
        except KeyboardInterrupt:
            log("\nDaemon stopped by user")
            break
        except Exception as e:
            log(f"Error in daemon loop: {e}", "ERROR")
            time.sleep(60)  # Wait a minute before retrying


def main():
    global DEFAULT_HOST, DEFAULT_PORT
    
    parser = argparse.ArgumentParser(description="Exit Order Service")
    parser.add_argument("--daemon", action="store_true", help="Run continuously during market hours")
    parser.add_argument("--status", action="store_true", help="Show pending orders status")
    parser.add_argument("--dry-run", action="store_true", help="Preview without placing orders")
    parser.add_argument("--host", default=DEFAULT_HOST, help="IB Gateway host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="IB Gateway port")
    
    args = parser.parse_args()
    
    DEFAULT_HOST = args.host
    DEFAULT_PORT = args.port
    
    if args.status:
        show_status()
    elif args.daemon:
        run_daemon()
    else:
        # Single check
        result = check_and_place_orders(dry_run=args.dry_run)
        
        if result["placed_orders"]:
            print(f"\n✓ Placed {len(result['placed_orders'])} order(s)")
        elif result["pending_orders"]:
            print(f"\n⏳ {len(result['pending_orders'])} order(s) still pending - target too far from market")


if __name__ == "__main__":
    main()
