#!/usr/bin/env python3
"""
Exit Orders Handler - Places pending exit orders when IB will accept them.

IB rejects limit orders >40% from current market price. This handler:
- Monitors pending exit orders in trade_log.json
- Checks current market prices
- Places orders when they're within the 40% threshold
- Updates trade_log.json with order IDs
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from ib_insync import Option, LimitOrder

from .base import BaseHandler
from clients.ib_client import IBClient, DEFAULT_HOST

logger = logging.getLogger(__name__)

# Default paths
DEFAULT_TRADE_LOG = Path(__file__).parent.parent.parent.parent / "data" / "trade_log.json"
DEFAULT_IB_PORT = 4001
DEFAULT_CLIENT_ID = 71


class ExitOrdersHandler(BaseHandler):
    """Place pending exit orders when IB will accept them."""
    
    name = "exit_orders"
    interval_seconds = 300  # Check every 5 minutes
    
    def __init__(
        self,
        trade_log_path: Optional[Path] = None,
        ib_port: int = DEFAULT_IB_PORT,
        client_id: int = DEFAULT_CLIENT_ID,
        max_gap_pct: float = 0.40
    ):
        super().__init__()
        self.trade_log_path = trade_log_path or DEFAULT_TRADE_LOG
        self.ib_port = ib_port
        self.client_id = client_id
        self.max_gap_pct = max_gap_pct
    
    def _load_pending_orders(self) -> List[Dict]:
        """Load pending exit orders from trade_log.json."""
        pending = []
        
        if not self.trade_log_path.exists():
            return pending
        
        try:
            trade_log = json.loads(self.trade_log_path.read_text())
            
            for trade in trade_log.get("trades", []):
                exit_orders = trade.get("exit_orders", {})
                
                # Check target orders
                target = exit_orders.get("target", {})
                if target.get("status") == "PENDING":
                    pending.append({
                        "trade_id": trade.get("id"),
                        "ticker": trade.get("ticker"),
                        "structure": trade.get("structure"),
                        "order_type": "target",
                        "target_price": target.get("price"),
                        "contracts": target.get("contracts"),
                        "contract_spec": target.get("contract_spec"),
                        "action": "SELL"  # Exit orders are sells
                    })
                
                # Check stop orders
                stop = exit_orders.get("stop", {})
                if stop.get("status") == "PENDING":
                    pending.append({
                        "trade_id": trade.get("id"),
                        "ticker": trade.get("ticker"),
                        "structure": trade.get("structure"),
                        "order_type": "stop",
                        "target_price": stop.get("price"),
                        "contracts": stop.get("contracts"),
                        "contract_spec": stop.get("contract_spec"),
                        "action": "SELL"
                    })
                    
        except Exception as e:
            logger.error(f"Failed to load trade log: {e}")
        
        return pending
    
    def _can_place_order(self, current_price: float, target_price: float) -> bool:
        """Check if order is within IB's acceptable gap."""
        if current_price <= 0:
            return False
        
        gap_pct = abs(target_price - current_price) / current_price
        return gap_pct <= self.max_gap_pct
    
    def _update_trade_log(self, trade_id: int, order_type: str, order_id: int) -> None:
        """Update trade_log.json with placed order ID."""
        try:
            trade_log = json.loads(self.trade_log_path.read_text())
            
            for trade in trade_log.get("trades", []):
                if trade.get("id") == trade_id:
                    exit_orders = trade.get("exit_orders", {})
                    if order_type in exit_orders:
                        exit_orders[order_type]["status"] = "PLACED"
                        exit_orders[order_type]["order_id"] = order_id
                        exit_orders[order_type]["placed_at"] = datetime.now().isoformat()
                    trade["exit_orders"] = exit_orders
                    break
            
            self.trade_log_path.write_text(json.dumps(trade_log, indent=2))
            logger.info(f"Updated trade log: trade {trade_id} {order_type} -> order #{order_id}")
            
        except Exception as e:
            logger.error(f"Failed to update trade log: {e}")
    
    def execute(self) -> Dict[str, Any]:
        """
        Check pending orders and place those within threshold.

        Returns:
            Dict with orders checked, placed, and skipped
        """
        result = {
            "orders_checked": 0,
            "orders_placed": 0,
            "orders_skipped": 0,
            "placed": [],
            "skipped": [],
            "timestamp": datetime.now().isoformat()
        }

        pending = self._load_pending_orders()

        if not pending:
            logger.debug("No pending exit orders")
            return result

        result["orders_checked"] = len(pending)

        client = IBClient()

        try:
            client.connect(host=DEFAULT_HOST, port=self.ib_port, client_id=self.client_id)
            logger.debug("Connected to IB")

            for order_info in pending:
                ticker = order_info["ticker"]
                target_price = order_info["target_price"]
                contracts = order_info.get("contracts", 1)
                spec = order_info.get("contract_spec", {})

                # Build contract
                if spec:
                    contract = Option(
                        symbol=spec.get("symbol", ticker),
                        lastTradeDateOrContractMonth=spec.get("expiry"),
                        strike=spec.get("strike"),
                        right=spec.get("right"),
                        exchange="SMART",
                        currency="USD"
                    )

                    qualified = client.qualify_contracts(contract)
                    if not qualified:
                        logger.warning(f"Could not qualify contract for {ticker}")
                        result["orders_skipped"] += 1
                        result["skipped"].append({
                            "ticker": ticker,
                            "reason": "contract_qualification_failed"
                        })
                        continue

                    contract = qualified[0]

                    # Get current price
                    ticker_data = client.get_quote(contract)
                    client.sleep(2)

                    bid = ticker_data.bid if ticker_data.bid and ticker_data.bid > 0 else 0
                    ask = ticker_data.ask if ticker_data.ask and ticker_data.ask > 0 else 0
                    mid = (bid + ask) / 2 if bid and ask else 0

                    client.cancel_market_data(contract)

                    if mid <= 0:
                        logger.warning(f"No market data for {contract.localSymbol}")
                        result["orders_skipped"] += 1
                        result["skipped"].append({
                            "ticker": ticker,
                            "contract": contract.localSymbol,
                            "reason": "no_market_data"
                        })
                        continue

                    # Check if within threshold
                    if self._can_place_order(mid, target_price):
                        # Place the order
                        limit_order = LimitOrder(
                            action="SELL",
                            totalQuantity=contracts,
                            lmtPrice=target_price,
                            tif="GTC"
                        )

                        trade = client.place_order(contract, limit_order)
                        client.sleep(1)

                        order_id = trade.order.orderId

                        logger.info(
                            f"Placed exit order: SELL {contracts}x {contract.localSymbol} "
                            f"@ ${target_price:.2f} (Order #{order_id})"
                        )

                        result["orders_placed"] += 1
                        result["placed"].append({
                            "ticker": ticker,
                            "contract": contract.localSymbol,
                            "order_id": order_id,
                            "price": target_price,
                            "current_mid": mid
                        })

                        # Update trade log
                        self._update_trade_log(
                            order_info["trade_id"],
                            order_info["order_type"],
                            order_id
                        )
                    else:
                        gap_pct = abs(target_price - mid) / mid * 100
                        logger.debug(
                            f"Skipping {ticker}: gap {gap_pct:.1f}% exceeds {self.max_gap_pct*100:.0f}% threshold"
                        )
                        result["orders_skipped"] += 1
                        result["skipped"].append({
                            "ticker": ticker,
                            "contract": contract.localSymbol,
                            "target": target_price,
                            "current_mid": mid,
                            "gap_pct": gap_pct,
                            "reason": "gap_too_large"
                        })
                else:
                    logger.warning(f"No contract spec for {ticker}")
                    result["orders_skipped"] += 1
                    result["skipped"].append({
                        "ticker": ticker,
                        "reason": "no_contract_spec"
                    })

        except Exception as e:
            logger.error(f"Exit orders error: {e}")
            result["error"] = str(e)
        finally:
            client.disconnect()
            logger.debug("Disconnected from IB")
        
        return result
