#!/usr/bin/env python3
"""
IB Order Placement — JSON-in / JSON-out for web API.

Places a limit order via IB, waits briefly for acknowledgement, returns JSON.
Does NOT monitor fills or log trades (web layer handles that).

Usage:
  python3 scripts/ib_place_order.py --json '{"type":"stock","symbol":"AAPL","action":"BUY","quantity":100,"limitPrice":214.50,"tif":"DAY"}'
  python3 scripts/ib_place_order.py --json '{"type":"option","symbol":"GOOG","action":"BUY","quantity":10,"limitPrice":9.00,"tif":"GTC","expiry":"20260417","strike":315,"right":"C"}'
"""

import json
import sys
from pathlib import Path

try:
    from ib_insync import Stock, Option, Contract, ComboLeg, LimitOrder, TagValue, util
except ImportError:
    print(json.dumps({"status": "error", "message": "ib_insync not installed"}))
    sys.exit(1)

# Add project root + scripts dir to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from clients.ib_client import IBClient, CLIENT_IDS, DEFAULT_HOST, DEFAULT_GATEWAY_PORT

CLIENT_ID = CLIENT_IDS.get("ib_place_order", 26)
PORT = DEFAULT_GATEWAY_PORT


def place_order(params: dict) -> dict:
    """Place a limit order and return result as dict."""
    order_type = params.get("type", "stock")
    symbol = params["symbol"].upper()
    action = params["action"].upper()
    quantity = int(params["quantity"])
    limit_price = float(params["limitPrice"])
    tif = params.get("tif", "DAY").upper()

    client = IBClient()

    try:
        client.connect(host=DEFAULT_HOST, port=PORT, client_id=CLIENT_ID, timeout=10)
    except Exception as e:
        return {"status": "error", "message": f"Connection failed: {e}"}

    try:
        # Build contract
        if order_type == "combo":
            legs_data = params["legs"]
            options = []
            for leg in legs_data:
                opt = Option(
                    symbol=symbol,
                    lastTradeDateOrContractMonth=leg["expiry"],
                    strike=float(leg["strike"]),
                    right=leg["right"],
                    exchange="SMART",
                    currency="USD",
                )
                options.append(opt)

            qualified = client.qualify_contracts(*options)
            if len(qualified) != len(options):
                return {"status": "error", "message": f"Could not qualify all combo legs for {symbol}"}

            combo = Contract()
            combo.symbol = symbol
            combo.secType = "BAG"
            combo.currency = "USD"
            combo.exchange = "SMART"

            combo_legs = []
            for i, leg in enumerate(legs_data):
                cl = ComboLeg()
                cl.conId = qualified[i].conId
                cl.ratio = int(leg.get("ratio", 1))
                cl.action = leg["action"].upper()
                cl.exchange = "SMART"
                combo_legs.append(cl)

            combo.comboLegs = combo_legs
            contract = combo

        elif order_type == "option":
            expiry = params["expiry"]
            strike = float(params["strike"])
            right = params["right"]
            contract = Option(
                symbol=symbol,
                lastTradeDateOrContractMonth=expiry,
                strike=strike,
                right=right,
                exchange="SMART",
                currency="USD",
            )
            qualified = client.qualify_contracts(contract)
            if not qualified:
                return {"status": "error", "message": f"Could not qualify contract: {symbol}"}
            contract = qualified[0]

        else:
            contract = Stock(symbol, "SMART", "USD")
            qualified = client.qualify_contracts(contract)
            if not qualified:
                return {"status": "error", "message": f"Could not qualify contract: {symbol}"}
            contract = qualified[0]

        # Build order
        order = LimitOrder(
            action=action,
            totalQuantity=quantity,
            lmtPrice=limit_price,
            tif=tif,
            outsideRth=False,
        )

        if order_type == "combo":
            order.smartComboRoutingParams = [TagValue("NonGuaranteed", "1")]

        # Place
        trade = client.place_order(contract, order)
        client.sleep(2)  # Wait for IB acknowledgement

        order_id = trade.order.orderId
        perm_id = trade.order.permId
        status = trade.orderStatus.status if trade.orderStatus else "Unknown"

        return {
            "status": "ok",
            "orderId": order_id,
            "permId": perm_id,
            "initialStatus": status,
            "message": f"{action} {quantity} {symbol} @ ${limit_price:.2f} — {status}",
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}

    finally:
        client.disconnect()


def main():
    if "--json" not in sys.argv:
        print(json.dumps({"status": "error", "message": "Usage: --json '{...}'"}))
        sys.exit(1)

    json_idx = sys.argv.index("--json")
    if json_idx + 1 >= len(sys.argv):
        print(json.dumps({"status": "error", "message": "Missing JSON argument after --json"}))
        sys.exit(1)

    try:
        params = json.loads(sys.argv[json_idx + 1])
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "message": f"Invalid JSON: {e}"}))
        sys.exit(1)

    # Validate required fields
    required = ["symbol", "action", "quantity", "limitPrice"]
    missing = [f for f in required if f not in params]
    if missing:
        print(json.dumps({"status": "error", "message": f"Missing fields: {', '.join(missing)}"}))
        sys.exit(1)

    if params.get("type") == "option":
        opt_required = ["expiry", "strike", "right"]
        opt_missing = [f for f in opt_required if f not in params]
        if opt_missing:
            print(json.dumps({"status": "error", "message": f"Option missing: {', '.join(opt_missing)}"}))
            sys.exit(1)

    if params.get("type") == "combo":
        legs = params.get("legs")
        if not legs or not isinstance(legs, list) or len(legs) < 2:
            print(json.dumps({"status": "error", "message": "Combo requires 'legs' array with 2+ entries"}))
            sys.exit(1)
        leg_required = ["expiry", "strike", "right", "action"]
        for i, leg in enumerate(legs):
            leg_missing = [f for f in leg_required if f not in leg]
            if leg_missing:
                print(json.dumps({"status": "error", "message": f"Leg {i} missing: {', '.join(leg_missing)}"}))
                sys.exit(1)

    result = place_order(params)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
