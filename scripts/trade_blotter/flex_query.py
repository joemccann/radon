#!/usr/bin/env python3
"""
Flex Query - Fetch historical executions from Interactive Brokers.

Flex Queries allow fetching up to 365 days of trade history.

Setup (one-time):
1. Login to IB Account Management: https://www.interactivebrokers.com/sso/Login
2. Go to: Reports > Flex Queries
3. Click "Create" under Trade Confirmation Flex Query
4. Configure:
   - Query Name: "Trade History"
   - Format: XML
   - Include: Trades section with all fields
5. Save and note the Query ID
6. Go to: Reports > Settings > Flex Web Service
7. Generate a token and note it

Usage:
    python3 flex_query.py --token YOUR_TOKEN --query-id YOUR_QUERY_ID
    python3 flex_query.py --setup  # Interactive setup guide
"""
import argparse
import os
import sys
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import Execution, Trade, TradeBlotter, Side, SecurityType
from formatting import format_currency, format_pnl


def _http_get_text(url: str, params: dict, timeout: int = 30) -> str:
    """Small stdlib HTTP helper so the CLI works without third-party deps."""
    request_url = f"{url}?{urlencode(params)}"
    try:
        with urlopen(request_url, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        raise RuntimeError(f"HTTP {e.code}: {body[:300] or e.reason}") from e
    except URLError as e:
        reason = getattr(e, "reason", e)
        raise RuntimeError(f"Request failed: {reason}") from e


class FlexQueryFetcher:
    """
    Fetches historical executions via IB Flex Query.
    """
    
    FLEX_SERVICE_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService"
    
    def __init__(self, token: str, query_id: str):
        self.token = token
        self.query_id = query_id
    
    def fetch_executions(self, days_back: int = 30) -> List[Execution]:
        """Fetch executions from Flex Query."""
        print(f"Requesting Flex Query report...")
        
        # Step 1: Request the report
        request_url = f"{self.FLEX_SERVICE_URL}.SendRequest"
        params = {
            "t": self.token,
            "q": self.query_id,
            "v": "3",
        }

        response_text = _http_get_text(request_url, params, timeout=30)
        
        # Parse response
        try:
            root = ET.fromstring(response_text)
        except ET.ParseError:
            raise RuntimeError(f"Invalid XML response: {response_text[:500]}")
        
        status = root.find(".//Status")
        if status is None:
            # Check if it's a direct FlexStatement response
            if root.tag == "FlexQueryResponse" or root.find(".//FlexStatements") is not None:
                return self._parse_xml(response_text)
            raise RuntimeError(f"Unexpected response format: {response_text[:500]}")
        
        if status.text != "Success":
            error_msg = root.find(".//ErrorMessage")
            error_code = root.find(".//ErrorCode")
            raise RuntimeError(
                f"Flex Query request failed: {error_msg.text if error_msg is not None else 'Unknown error'} "
                f"(code: {error_code.text if error_code is not None else 'N/A'})"
            )
        
        reference_code = root.find(".//ReferenceCode")
        if reference_code is None:
            raise RuntimeError("No reference code in response")
        
        print(f"Report requested. Reference: {reference_code.text}")
        print("Waiting for report generation...")
        
        # Step 2: Poll for the report
        statement_url = f"{self.FLEX_SERVICE_URL}.GetStatement"
        max_attempts = 20
        
        for attempt in range(max_attempts):
            time.sleep(3)  # Wait before polling
            
            params = {
                "t": self.token,
                "q": reference_code.text,
                "v": "3",
            }

            try:
                response_text = _http_get_text(statement_url, params, timeout=30)
            except RuntimeError:
                print(f"  Attempt {attempt + 1}: Request failed, retrying...")
                continue
            
            # Check if still processing
            if "<Status>Success</Status>" not in response_text and "<FlexStatements>" not in response_text:
                print(f"  Attempt {attempt + 1}: Still processing...")
                continue
            
            print("Report ready. Parsing...")
            return self._parse_xml(response_text)
        
        raise RuntimeError("Flex Query timed out after 60 seconds")
    
    def _parse_xml(self, xml_content: str) -> List[Execution]:
        """Parse Flex Query XML response into executions."""
        root = ET.fromstring(xml_content)
        executions = []
        
        # Find all Trade elements
        for trade in root.findall(".//Trade"):
            try:
                exec = self._parse_trade_element(trade)
                if exec:
                    executions.append(exec)
            except Exception as e:
                print(f"Warning: Failed to parse trade: {e}")
                continue
        
        # Sort by time
        executions.sort(key=lambda e: e.time)
        
        return executions
    
    def _parse_trade_element(self, trade: ET.Element) -> Optional[Execution]:
        """Parse a single Trade XML element."""
        symbol = trade.get("symbol")
        if not symbol:
            return None
        
        # Security type
        asset_category = trade.get("assetCategory", "STK")
        sec_type_map = {
            "STK": SecurityType.STOCK,
            "OPT": SecurityType.OPTION,
            "FUT": SecurityType.FUTURE,
            "CASH": SecurityType.FOREX,
            "FOP": SecurityType.OPTION,  # Future options
        }
        sec_type = sec_type_map.get(asset_category, SecurityType.STOCK)
        
        # Parse datetime
        datetime_str = trade.get("dateTime") or trade.get("tradeDate")
        if not datetime_str:
            return None
        
        try:
            if ";" in datetime_str:
                exec_time = datetime.strptime(datetime_str, "%Y-%m-%d;%H:%M:%S")
            elif "T" in datetime_str:
                exec_time = datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
            elif " " in datetime_str:
                exec_time = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S")
            else:
                exec_time = datetime.strptime(datetime_str, "%Y%m%d")
        except ValueError as e:
            print(f"Warning: Could not parse date '{datetime_str}': {e}")
            return None
        
        # Parse side
        buy_sell = trade.get("buySell") or trade.get("side", "")
        if buy_sell.upper() in ("BUY", "BOT", "B"):
            side = Side.BUY
        elif buy_sell.upper() in ("SELL", "SLD", "S"):
            side = Side.SELL
        else:
            return None
        
        # Parse quantities and prices
        quantity_str = trade.get("quantity") or trade.get("shares", "0")
        price_str = trade.get("tradePrice") or trade.get("price", "0")
        commission_str = trade.get("ibCommission") or trade.get("commission", "0")
        
        quantity = Decimal(str(abs(float(quantity_str))))
        price = Decimal(price_str)
        commission = Decimal(str(abs(float(commission_str))))
        
        # Option fields
        strike = None
        right = None
        expiry = None
        
        if sec_type == SecurityType.OPTION:
            strike_str = trade.get("strike", "0")
            if strike_str and float(strike_str) > 0:
                strike = Decimal(strike_str)
            
            right = trade.get("putCall") or trade.get("right", "")
            if right:
                right = right[0].upper()  # 'C' or 'P'
            
            expiry = trade.get("expiry") or trade.get("lastTradeDateOrContractMonth", "")
        
        trade_id = trade.get("tradeID") or trade.get("execId") or f"{symbol}_{datetime_str}"
        
        return Execution(
            exec_id=trade_id,
            time=exec_time,
            symbol=symbol,
            sec_type=sec_type,
            side=side,
            quantity=quantity,
            price=price,
            commission=commission,
            strike=strike,
            right=right,
            expiry=expiry,
        )


def group_executions_to_trades(executions: List[Execution]) -> List[Trade]:
    """Group executions into trades by contract."""
    from collections import defaultdict
    
    trades_map = defaultdict(lambda: {"executions": [], "sec_type": None, "symbol": None})
    
    for exec in executions:
        key = exec.contract_desc
        trades_map[key]["executions"].append(exec)
        trades_map[key]["sec_type"] = exec.sec_type
        trades_map[key]["symbol"] = exec.symbol
    
    trades = []
    for key, data in trades_map.items():
        data["executions"].sort(key=lambda e: e.time)
        trades.append(Trade(
            symbol=data["symbol"],
            contract_desc=key,
            sec_type=data["sec_type"],
            executions=data["executions"],
        ))
    
    return trades


def print_setup_guide():
    """Print interactive setup guide for Flex Query."""
    print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        FLEX QUERY SETUP GUIDE                                 ║
╚══════════════════════════════════════════════════════════════════════════════╝

To fetch historical trades from IB, you need to set up a Flex Query.
This is a one-time setup in IB Account Management.

STEP 1: Login to IB Account Management
────────────────────────────────────────
   https://www.interactivebrokers.com/sso/Login

STEP 2: Create a Flex Query
────────────────────────────────────────
   Navigate to: Reports > Flex Queries
   
   Click: "+ Create" under "Activity Flex Query"
   
   Configure:
   ┌─────────────────────────────────────────────────────────┐
   │ Query Name: Trade History                               │
   │ Format: XML                                             │
   │ Period: Last 30 Days (or custom)                        │
   │                                                         │
   │ Sections to include:                                    │
   │   ☑ Trades                                              │
   │     - Select ALL fields                                 │
   │   ☑ Cash Transactions (optional, for dividends)        │
   └─────────────────────────────────────────────────────────┘
   
   Click: Save
   
   Note the Query ID shown (e.g., 123456)

STEP 3: Get Flex Web Service Token
────────────────────────────────────────
   Navigate to: Reports > Settings > Flex Web Service
   
   Click: "Generate Token" (or copy existing)
   
   Note the token (looks like a long alphanumeric string)
   
   ⚠️  Keep this token secure - it provides API access to your account

STEP 4: Save Credentials
────────────────────────────────────────
   Option A: Environment variables (recommended)
   
   export IB_FLEX_TOKEN="your_token_here"
   export IB_FLEX_QUERY_ID="123456"
   
   Option B: Pass as arguments
   
   python3 flex_query.py --token YOUR_TOKEN --query-id 123456

STEP 5: Test
────────────────────────────────────────
   python3 flex_query.py --token YOUR_TOKEN --query-id YOUR_QUERY_ID

══════════════════════════════════════════════════════════════════════════════
""")


def print_blotter(blotter: TradeBlotter, filter_symbol: str = None):
    """Print blotter with optional symbol filter."""
    print("=" * 70)
    print("HISTORICAL TRADE BLOTTER")
    print(f"As of: {blotter.as_of.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    trades = blotter.trades
    if filter_symbol:
        trades = [t for t in trades if t.symbol == filter_symbol.upper()]
        print(f"Filtered to: {filter_symbol.upper()}")
    
    # Closed trades
    closed = [t for t in trades if t.is_closed]
    if closed:
        print(f"\n🔒 CLOSED TRADES ({len(closed)})")
        print("-" * 50)
        for trade in closed:
            print(f"\n  {trade.contract_desc}")
            print(f"     Executions: {len(trade.executions)}")
            print(f"     Commissions: {format_currency(trade.total_commission)}")
            print(f"     Realized P&L: {format_pnl(trade.realized_pnl)}")
            
            # Show executions
            for e in trade.executions:
                side_icon = "🟢" if e.side == Side.BUY else "🔴"
                print(f"        {side_icon} {e.time.strftime('%Y-%m-%d %H:%M')} | "
                      f"{e.side.value} {e.quantity}x @ ${e.price:.2f} | "
                      f"Fee: ${e.commission:.2f}")
    
    # Open trades
    open_trades = [t for t in trades if not t.is_closed]
    if open_trades:
        print(f"\n📂 OPEN POSITIONS ({len(open_trades)})")
        print("-" * 50)
        for trade in open_trades:
            print(f"\n  {trade.contract_desc}")
            print(f"     Net Qty: {trade.net_quantity}")
            print(f"     Cost Basis: {format_currency(trade.cost_basis)}")
            print(f"     Commissions: {format_currency(trade.total_commission)}")
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    total_realized = sum(t.realized_pnl or Decimal(0) for t in closed)
    total_commission = sum(t.total_commission for t in trades)
    
    print(f"  Closed Trades:     {len(closed)}")
    print(f"  Open Positions:    {len(open_trades)}")
    print(f"  Total Commissions: {format_currency(total_commission)}")
    print(f"  Total Realized:    {format_pnl(total_realized)}")
    
    # Spread analysis
    spreads = blotter.get_spreads()
    if spreads:
        print(f"\n  SPREADS ({len(spreads)})")
        for spread in spreads:
            status = "CLOSED" if spread.is_closed else "OPEN"
            pnl = spread.realized_pnl if spread.is_closed else spread.total_cash_flow
            print(f"     • {spread.name}: {format_pnl(pnl)} [{status}]")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch historical trades from IB via Flex Query",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 flex_query.py --setup                          # Show setup guide
  python3 flex_query.py --token XXX --query-id 123456    # Fetch trades
  python3 flex_query.py --symbol EWY                     # Filter by symbol
        """
    )
    parser.add_argument("--setup", action="store_true", help="Show setup guide")
    parser.add_argument("--token", help="Flex Web Service token (or IB_FLEX_TOKEN env)")
    parser.add_argument("--query-id", help="Flex Query ID (or IB_FLEX_QUERY_ID env)")
    parser.add_argument("--symbol", help="Filter results to specific symbol")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    
    args = parser.parse_args()
    
    if args.setup:
        print_setup_guide()
        return 0
    
    # Get credentials
    token = args.token or os.environ.get("IB_FLEX_TOKEN")
    query_id = args.query_id or os.environ.get("IB_FLEX_QUERY_ID")
    
    if not token or not query_id:
        print("Error: Flex Query credentials required.")
        print("       Use --token and --query-id, or set IB_FLEX_TOKEN and IB_FLEX_QUERY_ID")
        print("       Run with --setup for configuration guide.")
        return 1
    
    try:
        # Fetch executions
        fetcher = FlexQueryFetcher(token=token, query_id=query_id)
        executions = fetcher.fetch_executions()
        
        print(f"Fetched {len(executions)} executions")
        
        # Group into trades
        trades = group_executions_to_trades(executions)
        
        # Build blotter
        blotter = TradeBlotter(trades=trades, as_of=datetime.now())
        
        if args.json:
            import json
            from cli import blotter_to_dict, DecimalEncoder
            print(json.dumps(blotter_to_dict(blotter), cls=DecimalEncoder, indent=2))
        else:
            print_blotter(blotter, filter_symbol=args.symbol)
        
        return 0
        
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
