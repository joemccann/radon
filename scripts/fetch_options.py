#!/usr/bin/env python3
"""
Fetch options chain and flow data.
Priority: IB (chain) → UW (chain + flow) → Yahoo (chain only)

Usage:
    python3 scripts/fetch_options.py RMBS
    python3 scripts/fetch_options.py RMBS --dte-min 14 --dte-max 60
    python3 scripts/fetch_options.py RMBS --port 7497  # IB paper trading
    python3 scripts/fetch_options.py RMBS --source uw  # Force Unusual Whales
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

import requests

# Configuration
UW_TOKEN = os.environ.get("UW_TOKEN")
UW_BASE = "https://api.unusualwhales.com"
IB_PORTS = [4001, 7496, 7497, 4002]  # Gateway Live, TWS Live, TWS Paper, Gateway Paper

# Try to import ib_insync
try:
    from ib_insync import IB, Stock, util
    IB_AVAILABLE = True
except ImportError:
    IB_AVAILABLE = False


def check_ib_connection(port: int) -> bool:
    """Check if IB is available on the given port."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        result = sock.connect_ex(('127.0.0.1', port))
        return result == 0
    finally:
        sock.close()


def fetch_ib_options(ticker: str, port: int = 4001) -> Optional[Dict]:
    """
    Fetch options chain from Interactive Brokers.
    Returns basic chain info (IB doesn't have flow data).
    """
    if not IB_AVAILABLE:
        return None
    
    # Find available port
    available_port = None
    for p in ([port] if port else IB_PORTS):
        if check_ib_connection(p):
            available_port = p
            break
    
    if not available_port:
        return None
    
    try:
        ib = IB()
        ib.connect('127.0.0.1', available_port, clientId=98, readonly=True)
        
        stock = Stock(ticker, 'SMART', 'USD')
        ib.qualifyContracts(stock)
        
        # Get current price
        ticker_data = ib.reqMktData(stock, '', False, False)
        ib.sleep(1)
        spot_price = ticker_data.last if ticker_data.last else ticker_data.close
        
        # Get options chains info
        chains = ib.reqSecDefOptParams(stock.symbol, '', stock.secType, stock.conId)
        
        result = {
            "source": "ib",
            "port": available_port,
            "spot_price": spot_price,
            "chains_available": len(chains) > 0,
            "expirations": [],
            "strikes": []
        }
        
        if chains:
            chain = chains[0]
            result["expirations"] = sorted(chain.expirations)[:10]  # Next 10 expirations
            result["strikes"] = sorted([s for s in chain.strikes if abs(s - spot_price) < spot_price * 0.3])
        
        ib.disconnect()
        return result
        
    except Exception as e:
        return {"source": "ib", "error": str(e)}


def fetch_uw_chain(ticker: str) -> Optional[Dict]:
    """Fetch options chain activity from Unusual Whales."""
    if not UW_TOKEN:
        return None
    
    headers = {"Authorization": f"Bearer {UW_TOKEN}"}
    
    try:
        resp = requests.get(
            f"{UW_BASE}/api/stock/{ticker}/option-contracts",
            headers=headers,
            timeout=10
        )
        
        if resp.status_code != 200:
            return {"error": f"UW API returned {resp.status_code}"}
        
        data = resp.json().get("data", [])
        
        if not data:
            return {"error": "No options chain data"}
        
        # Parse chain data
        call_premium = 0
        put_premium = 0
        call_volume = 0
        put_volume = 0
        call_bid_vol = 0
        call_ask_vol = 0
        put_bid_vol = 0
        put_ask_vol = 0
        call_oi = 0
        put_oi = 0
        contracts = []
        
        for c in data[:50]:  # Top 50 most active
            sym = c.get("option_symbol", "")
            prem = float(c.get("total_premium", 0))
            vol = int(c.get("volume", 0))
            oi = int(c.get("open_interest", 0))
            bid_vol = int(c.get("bid_volume", 0))
            ask_vol = int(c.get("ask_volume", 0))
            iv = float(c.get("implied_volatility", 0))
            
            # Parse option symbol for type
            is_call = "C00" in sym or "C0" in sym.split(ticker)[-1][:5] if ticker in sym else False
            is_put = "P00" in sym or "P0" in sym.split(ticker)[-1][:5] if ticker in sym else False
            
            # Fallback: check position in symbol
            if not is_call and not is_put:
                # Standard OCC format: SYMBOL + YYMMDD + C/P + STRIKE
                parts = sym.replace(ticker, "")
                if len(parts) >= 7:
                    type_char = parts[6] if len(parts) > 6 else ""
                    is_call = type_char == "C"
                    is_put = type_char == "P"
            
            contract_info = {
                "symbol": sym,
                "type": "call" if is_call else "put" if is_put else "unknown",
                "volume": vol,
                "open_interest": oi,
                "bid_volume": bid_vol,
                "ask_volume": ask_vol,
                "premium": prem,
                "iv": round(iv * 100, 1) if iv < 5 else round(iv, 1),  # Handle decimal vs percent
                "bid": c.get("nbbo_bid"),
                "ask": c.get("nbbo_ask")
            }
            contracts.append(contract_info)
            
            if is_call:
                call_premium += prem
                call_volume += vol
                call_bid_vol += bid_vol
                call_ask_vol += ask_vol
                call_oi += oi
            elif is_put:
                put_premium += prem
                put_volume += vol
                put_bid_vol += bid_vol
                put_ask_vol += ask_vol
                put_oi += oi
        
        # Calculate ratios and bias
        total_premium = call_premium + put_premium
        pc_ratio = put_premium / call_premium if call_premium > 0 else float('inf')
        
        if pc_ratio > 2.0:
            bias = "BEARISH"
            bias_strength = min(100, int((pc_ratio - 1) * 25))
        elif pc_ratio > 1.2:
            bias = "LEAN_BEARISH"
            bias_strength = int((pc_ratio - 1) * 50)
        elif pc_ratio < 0.5:
            bias = "BULLISH"
            bias_strength = min(100, int((1/pc_ratio - 1) * 25))
        elif pc_ratio < 0.8:
            bias = "LEAN_BULLISH"
            bias_strength = int((1/pc_ratio - 1) * 50)
        else:
            bias = "NEUTRAL"
            bias_strength = 0
        
        return {
            "source": "uw",
            "call_premium": call_premium,
            "put_premium": put_premium,
            "total_premium": total_premium,
            "put_call_ratio": round(pc_ratio, 2),
            "call_volume": call_volume,
            "put_volume": put_volume,
            "call_oi": call_oi,
            "put_oi": put_oi,
            "call_bid_volume": call_bid_vol,
            "call_ask_volume": call_ask_vol,
            "put_bid_volume": put_bid_vol,
            "put_ask_volume": put_ask_vol,
            "bias": bias,
            "bias_strength": bias_strength,
            "top_contracts": contracts[:10]
        }
        
    except Exception as e:
        return {"error": str(e)}


def fetch_uw_flow(ticker: str, days: int = 7) -> Optional[Dict]:
    """Fetch options flow alerts from Unusual Whales."""
    if not UW_TOKEN:
        return None
    
    headers = {"Authorization": f"Bearer {UW_TOKEN}"}
    
    try:
        resp = requests.get(
            f"{UW_BASE}/api/option-trades/flow-alerts",
            params={"ticker_symbol": ticker, "limit": 100},
            headers=headers,
            timeout=10
        )
        
        if resp.status_code != 200:
            return {"error": f"UW API returned {resp.status_code}"}
        
        data = resp.json().get("data", [])
        
        if not data:
            return {"total_alerts": 0, "bias": "NO_DATA"}
        
        # Filter to recent days
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        call_premium = 0
        put_premium = 0
        call_bid_prem = 0
        call_ask_prem = 0
        put_bid_prem = 0
        put_ask_prem = 0
        sweep_premium = 0
        alerts = []
        
        for a in data:
            created = a.get("created_at", "")
            try:
                alert_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if alert_time.replace(tzinfo=None) < cutoff:
                    continue
            except:
                pass
            
            prem = float(a.get("total_premium", 0))
            bid_prem = float(a.get("total_bid_side_prem", 0))
            ask_prem = float(a.get("total_ask_side_prem", 0))
            opt_type = a.get("type", "").lower()
            has_sweep = a.get("has_sweep", False)
            
            alert_info = {
                "date": created[:10] if created else "unknown",
                "type": opt_type,
                "strike": a.get("strike"),
                "expiry": a.get("expiry"),
                "premium": prem,
                "bid_side_prem": bid_prem,
                "ask_side_prem": ask_prem,
                "volume": a.get("volume"),
                "oi": a.get("open_interest"),
                "is_sweep": has_sweep,
                "alert_rule": a.get("alert_rule"),
                "underlying_price": a.get("underlying_price")
            }
            alerts.append(alert_info)
            
            if opt_type == "call":
                call_premium += prem
                call_bid_prem += bid_prem
                call_ask_prem += ask_prem
            elif opt_type == "put":
                put_premium += prem
                put_bid_prem += bid_prem
                put_ask_prem += ask_prem
            
            if has_sweep:
                sweep_premium += prem
        
        total_premium = call_premium + put_premium
        
        # Determine flow bias
        if total_premium == 0:
            flow_bias = "NO_DATA"
            flow_strength = 0
        else:
            call_ratio = call_premium / total_premium
            
            # Bid-side = selling (closing longs or opening shorts)
            # Ask-side = buying (opening longs)
            total_bid = call_bid_prem + put_bid_prem
            total_ask = call_ask_prem + put_ask_prem
            
            if call_ratio > 0.65:
                flow_bias = "BULLISH"
                flow_strength = int((call_ratio - 0.5) * 200)
            elif call_ratio < 0.35:
                flow_bias = "BEARISH"
                flow_strength = int((0.5 - call_ratio) * 200)
            else:
                flow_bias = "NEUTRAL"
                flow_strength = 0
        
        # Analyze most recent alert for directional signal
        recent_bias = "NEUTRAL"
        if alerts:
            recent = alerts[0]
            if recent["type"] == "call" and recent["ask_side_prem"] > recent["bid_side_prem"]:
                recent_bias = "BULLISH"  # Buying calls
            elif recent["type"] == "put" and recent["ask_side_prem"] > recent["bid_side_prem"]:
                recent_bias = "BEARISH"  # Buying puts
            elif recent["type"] == "call" and recent["bid_side_prem"] > recent["ask_side_prem"]:
                recent_bias = "LEAN_BEARISH"  # Selling calls
            elif recent["type"] == "put" and recent["bid_side_prem"] > recent["ask_side_prem"]:
                recent_bias = "LEAN_BULLISH"  # Selling puts
        
        return {
            "source": "uw",
            "total_alerts": len(alerts),
            "call_premium": call_premium,
            "put_premium": put_premium,
            "total_premium": total_premium,
            "call_bid_premium": call_bid_prem,
            "call_ask_premium": call_ask_prem,
            "put_bid_premium": put_bid_prem,
            "put_ask_premium": put_ask_prem,
            "sweep_premium": sweep_premium,
            "flow_bias": flow_bias,
            "flow_strength": min(100, flow_strength),
            "recent_bias": recent_bias,
            "alerts": alerts[:10]  # Top 10 most recent
        }
        
    except Exception as e:
        return {"error": str(e)}


def fetch_yahoo_options(ticker: str) -> Optional[Dict]:
    """Fallback: Fetch basic options info from Yahoo Finance."""
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        
        expirations = stock.options
        if not expirations:
            return {"error": "No options available"}
        
        # Get nearest expiration chain
        chain = stock.option_chain(expirations[0])
        calls = chain.calls
        puts = chain.puts
        
        call_volume = calls['volume'].sum() if 'volume' in calls else 0
        put_volume = puts['volume'].sum() if 'volume' in puts else 0
        call_oi = calls['openInterest'].sum() if 'openInterest' in calls else 0
        put_oi = puts['openInterest'].sum() if 'openInterest' in puts else 0
        
        pc_ratio = put_volume / call_volume if call_volume > 0 else 0
        
        return {
            "source": "yahoo",
            "expirations": list(expirations[:10]),
            "nearest_expiry": expirations[0],
            "call_volume": int(call_volume),
            "put_volume": int(put_volume),
            "call_oi": int(call_oi),
            "put_oi": int(put_oi),
            "put_call_ratio": round(pc_ratio, 2),
            "note": "Volume-based only, no premium data"
        }
        
    except ImportError:
        return {"error": "yfinance not installed"}
    except Exception as e:
        return {"error": str(e)}


def fetch_options(ticker: str, dte_min: int = 20, dte_max: int = 45, 
                  port: int = None, source: str = None) -> Dict:
    """
    Fetch comprehensive options data following source priority.
    
    Returns:
        - chain: Options chain activity (volume, premium, OI)
        - flow: Institutional flow alerts (sweeps, blocks, unusual)
        - combined analysis
    """
    result = {
        "ticker": ticker.upper(),
        "fetched_at": datetime.now().isoformat(),
        "chain": None,
        "flow": None,
        "sources_tried": [],
        "analysis": {}
    }
    
    ticker = ticker.upper()
    
    # Source priority: IB → UW → Yahoo
    # But for flow data, only UW has it
    
    chain_data = None
    flow_data = None
    
    # 1. Try IB for basic chain info (expirations, strikes, spot price)
    ib_info = None
    if source in (None, "ib"):
        result["sources_tried"].append("ib")
        ib_info = fetch_ib_options(ticker, port or 4001)
    
    # 2. Try UW for chain + flow (preferred for volume/premium data)
    if source in (None, "uw") and UW_TOKEN:
        result["sources_tried"].append("uw")
        
        # Always try UW for chain - it has volume/premium data that IB doesn't provide
        uw_chain = fetch_uw_chain(ticker)
        if uw_chain and "error" not in uw_chain:
            chain_data = uw_chain
            # Merge in IB spot price if available
            if ib_info and "spot_price" in ib_info:
                chain_data["spot_price"] = ib_info["spot_price"]
        
        # Always get flow from UW (only source for this data)
        flow_data = fetch_uw_flow(ticker)
    
    # If forcing IB only, use IB data
    if source == "ib" and ib_info and not chain_data:
        chain_data = {"source": "ib", "available": True, **ib_info}
    
    # 3. Fallback to Yahoo for chain
    if source in (None, "yahoo") and not chain_data:
        result["sources_tried"].append("yahoo")
        yahoo_data = fetch_yahoo_options(ticker)
        if yahoo_data and "error" not in yahoo_data:
            chain_data = yahoo_data
    
    result["chain"] = chain_data
    result["flow"] = flow_data
    
    # Combined analysis
    analysis = {
        "chain_bias": "NO_DATA",
        "flow_bias": "NO_DATA",
        "combined_bias": "NO_DATA",
        "confidence": "LOW",
        "signals": []
    }
    
    if chain_data and "bias" in chain_data:
        analysis["chain_bias"] = chain_data["bias"]
        analysis["signals"].append(f"Chain: {chain_data['bias']} (P/C ratio: {chain_data.get('put_call_ratio', 'N/A')})")
    
    if flow_data and "flow_bias" in flow_data:
        analysis["flow_bias"] = flow_data["flow_bias"]
        analysis["signals"].append(f"Flow: {flow_data['flow_bias']} (strength: {flow_data.get('flow_strength', 0)})")
        
        if flow_data.get("recent_bias"):
            analysis["signals"].append(f"Recent: {flow_data['recent_bias']}")
    
    # Determine combined bias
    chain_bias = analysis["chain_bias"]
    flow_bias = analysis["flow_bias"]
    
    bias_map = {"BULLISH": 2, "LEAN_BULLISH": 1, "NEUTRAL": 0, "LEAN_BEARISH": -1, "BEARISH": -2, "NO_DATA": None}
    
    chain_score = bias_map.get(chain_bias)
    flow_score = bias_map.get(flow_bias)
    
    if chain_score is not None and flow_score is not None:
        combined_score = (chain_score + flow_score) / 2
        if combined_score >= 1.5:
            analysis["combined_bias"] = "BULLISH"
            analysis["confidence"] = "HIGH"
        elif combined_score >= 0.5:
            analysis["combined_bias"] = "LEAN_BULLISH"
            analysis["confidence"] = "MEDIUM"
        elif combined_score <= -1.5:
            analysis["combined_bias"] = "BEARISH"
            analysis["confidence"] = "HIGH"
        elif combined_score <= -0.5:
            analysis["combined_bias"] = "LEAN_BEARISH"
            analysis["confidence"] = "MEDIUM"
        else:
            analysis["combined_bias"] = "NEUTRAL"
            analysis["confidence"] = "LOW"
        
        # Check for conflicting signals
        if (chain_score > 0 and flow_score < 0) or (chain_score < 0 and flow_score > 0):
            analysis["signals"].append("⚠️ CONFLICTING: Chain and flow disagree")
            analysis["confidence"] = "LOW"
    elif chain_score is not None:
        analysis["combined_bias"] = chain_bias
        analysis["confidence"] = "LOW"
    elif flow_score is not None:
        analysis["combined_bias"] = flow_bias
        analysis["confidence"] = "LOW"
    
    result["analysis"] = analysis
    
    return result


def print_report(data: Dict):
    """Print formatted options report."""
    print("=" * 70)
    print(f"OPTIONS ANALYSIS: {data['ticker']}")
    print(f"Fetched: {data['fetched_at']}")
    print(f"Sources: {', '.join(data['sources_tried'])}")
    print("=" * 70)
    
    # Chain data
    chain = data.get("chain")
    if chain and "error" not in chain:
        print("\n📊 OPTIONS CHAIN ACTIVITY")
        print("-" * 70)
        
        if "call_premium" in chain:
            print(f"  Call Premium:    ${chain['call_premium']:>12,.0f}")
            print(f"  Put Premium:     ${chain['put_premium']:>12,.0f}")
            print(f"  Total Premium:   ${chain['total_premium']:>12,.0f}")
            print(f"  Put/Call Ratio:  {chain['put_call_ratio']:>12.2f}x")
            print(f"  Bias:            {chain['bias']:>12}")
        
        if "call_volume" in chain:
            print(f"\n  Call Volume:     {chain['call_volume']:>12,}")
            print(f"  Put Volume:      {chain['put_volume']:>12,}")
        
        if "call_oi" in chain:
            print(f"  Call OI:         {chain['call_oi']:>12,}")
            print(f"  Put OI:          {chain['put_oi']:>12,}")
        
        if "top_contracts" in chain and chain["top_contracts"]:
            print("\n  Top Active Contracts:")
            print(f"  {'Symbol':<25} {'Type':<6} {'Vol':>8} {'OI':>8} {'Premium':>12} {'IV':>6}")
            for c in chain["top_contracts"][:5]:
                print(f"  {c['symbol']:<25} {c['type']:<6} {c['volume']:>8,} {c['open_interest']:>8,} ${c['premium']:>10,.0f} {c['iv']:>5.1f}%")
    else:
        print("\n📊 OPTIONS CHAIN: NO DATA")
        if chain and "error" in chain:
            print(f"   Error: {chain['error']}")
    
    # Flow data
    flow = data.get("flow")
    if flow and "error" not in flow and flow.get("total_alerts", 0) > 0:
        print("\n📈 INSTITUTIONAL FLOW ALERTS")
        print("-" * 70)
        print(f"  Total Alerts:    {flow['total_alerts']:>12}")
        print(f"  Call Premium:    ${flow['call_premium']:>12,.0f}")
        print(f"  Put Premium:     ${flow['put_premium']:>12,.0f}")
        print(f"  Sweep Premium:   ${flow['sweep_premium']:>12,.0f}")
        print(f"  Flow Bias:       {flow['flow_bias']:>12} (strength: {flow['flow_strength']})")
        print(f"  Recent Bias:     {flow['recent_bias']:>12}")
        
        if "alerts" in flow and flow["alerts"]:
            print("\n  Recent Alerts:")
            print(f"  {'Date':<12} {'Type':<6} {'Strike':>8} {'Expiry':<12} {'Premium':>12} {'Sweep':<6}")
            for a in flow["alerts"][:5]:
                sweep = "YES" if a.get("is_sweep") else ""
                print(f"  {a['date']:<12} {a['type']:<6} ${a['strike']:>7} {a['expiry']:<12} ${a['premium']:>10,.0f} {sweep:<6}")
    else:
        print("\n📈 INSTITUTIONAL FLOW: NO DATA")
    
    # Analysis
    analysis = data.get("analysis", {})
    print("\n" + "=" * 70)
    print("🎯 COMBINED ANALYSIS")
    print("-" * 70)
    print(f"  Chain Bias:      {analysis.get('chain_bias', 'N/A')}")
    print(f"  Flow Bias:       {analysis.get('flow_bias', 'N/A')}")
    print(f"  Combined:        {analysis.get('combined_bias', 'N/A')}")
    print(f"  Confidence:      {analysis.get('confidence', 'N/A')}")
    
    if analysis.get("signals"):
        print("\n  Signals:")
        for sig in analysis["signals"]:
            print(f"    • {sig}")
    
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Fetch options chain and flow data")
    parser.add_argument("ticker", help="Stock ticker symbol")
    parser.add_argument("--dte-min", type=int, default=20, help="Minimum days to expiry")
    parser.add_argument("--dte-max", type=int, default=45, help="Maximum days to expiry")
    parser.add_argument("--port", type=int, help="IB port (default: auto-detect)")
    parser.add_argument("--source", choices=["ib", "uw", "yahoo"], help="Force specific data source")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    
    args = parser.parse_args()
    
    data = fetch_options(
        args.ticker,
        dte_min=args.dte_min,
        dte_max=args.dte_max,
        port=args.port,
        source=args.source
    )
    
    if args.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print_report(data)
        # Also output JSON for programmatic use
        print("\n# JSON Output:")
        print(json.dumps({
            "ticker": data["ticker"],
            "chain_bias": data["analysis"].get("chain_bias"),
            "flow_bias": data["analysis"].get("flow_bias"),
            "combined_bias": data["analysis"].get("combined_bias"),
            "confidence": data["analysis"].get("confidence"),
            "put_call_ratio": data.get("chain", {}).get("put_call_ratio"),
            "total_premium": data.get("chain", {}).get("total_premium"),
            "flow_alerts": data.get("flow", {}).get("total_alerts", 0)
        }, indent=2))


if __name__ == "__main__":
    main()
