"""
Trade Blotter / Reconciliation Service.

Fetches execution data from Interactive Brokers and calculates P&L.
Supports both real-time fills and historical Flex Query data.
"""
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from ib_insync import Fill

from models import Execution, Trade, TradeBlotter, Side, SecurityType

# Add scripts dir so clients package is importable
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from clients.ib_client import IBClient


def _http_get_text(url: str, params: dict, timeout: int = 30) -> str:
    """Small stdlib HTTP helper so historical fetchers do not require requests."""
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


class ExecutionFetcher(ABC):
    """Abstract base class for execution data fetching."""
    
    @abstractmethod
    def fetch_executions(self) -> List[Execution]:
        """Fetch all executions."""
        pass


class IBFetcher(ExecutionFetcher):
    """
    Fetches executions from Interactive Brokers via API.

    Supports:
    - Today's fills (real-time)
    - Completed orders
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 4001, client_id: int = 90):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.client = IBClient()

    def _connect(self):
        """Connect to IB Gateway/TWS."""
        if not self.client.is_connected():
            self.client.connect(host=self.host, port=self.port, client_id=self.client_id)

    def _disconnect(self):
        """Disconnect from IB."""
        self.client.disconnect()
    
    def _parse_fill(self, fill: Fill) -> Execution:
        """Convert IB Fill object to Execution."""
        contract = fill.contract
        exec_report = fill.execution
        comm_report = fill.commissionReport
        
        # Determine security type
        sec_type_map = {
            "STK": SecurityType.STOCK,
            "OPT": SecurityType.OPTION,
            "BAG": SecurityType.COMBO,
            "FUT": SecurityType.FUTURE,
            "CASH": SecurityType.FOREX,
        }
        sec_type = sec_type_map.get(contract.secType, SecurityType.STOCK)
        
        # Determine side
        side = Side.BUY if exec_report.side == "BOT" else Side.SELL
        
        # Get commission (may be 0 if not yet reported)
        commission = Decimal(str(comm_report.commission)) if comm_report else Decimal("0")
        
        # Handle option-specific fields
        strike = None
        right = None
        expiry = None
        
        if sec_type == SecurityType.OPTION:
            strike = Decimal(str(contract.strike)) if contract.strike else None
            right = contract.right if contract.right else None
            expiry = contract.lastTradeDateOrContractMonth if contract.lastTradeDateOrContractMonth else None
        
        return Execution(
            exec_id=exec_report.execId,
            time=exec_report.time,
            symbol=contract.symbol,
            sec_type=sec_type,
            side=side,
            quantity=Decimal(str(exec_report.shares)),
            price=Decimal(str(exec_report.price)),
            commission=commission,
            strike=strike,
            right=right,
            expiry=expiry,
        )
    
    def fetch_executions(self) -> List[Execution]:
        """Fetch all today's executions."""
        return self.fetch_today_executions()
    
    def fetch_today_executions(self) -> List[Execution]:
        """Fetch today's fills from IB."""
        self._connect()
        try:
            fills = self.client.get_fills()
            executions = []
            
            for fill in fills:
                # Skip combo/bag entries (we get individual legs)
                if fill.contract.secType == "BAG":
                    continue
                try:
                    exec = self._parse_fill(fill)
                    executions.append(exec)
                except Exception as e:
                    print(f"Warning: Failed to parse fill: {e}")
                    continue
            
            return executions
        finally:
            self._disconnect()


class FlexQueryFetcher(ExecutionFetcher):
    """
    Fetches historical executions via IB Flex Query.
    
    Flex Queries allow fetching up to 365 days of trade history.
    Requires setting up a Flex Query in Account Management.
    """
    
    FLEX_SERVICE_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService"
    
    def __init__(self, token: str, query_id: str):
        """
        Initialize Flex Query fetcher.
        
        Args:
            token: Flex Web Service Token from Account Management
            query_id: Flex Query ID to execute
        """
        self.token = token
        self.query_id = query_id
    
    def fetch_executions(self) -> List[Execution]:
        """Fetch executions via Flex Query."""
        # Step 1: Request the report
        request_url = f"{self.FLEX_SERVICE_URL}.SendRequest"
        params = {
            "t": self.token,
            "q": self.query_id,
            "v": "3",
        }

        response_text = _http_get_text(request_url, params)
        
        # Parse reference code from response
        root = ET.fromstring(response_text)
        status = root.find(".//Status")
        
        if status is None or status.text != "Success":
            error_msg = root.find(".//ErrorMessage")
            raise RuntimeError(f"Flex Query request failed: {error_msg.text if error_msg else 'Unknown error'}")
        
        reference_code = root.find(".//ReferenceCode").text
        
        # Step 2: Poll for the report (may take a few seconds)
        statement_url = f"{self.FLEX_SERVICE_URL}.GetStatement"
        max_attempts = 10
        
        for attempt in range(max_attempts):
            time.sleep(2)  # Wait before polling
            
            params = {
                "t": self.token,
                "q": reference_code,
                "v": "3",
            }

            response_text = _http_get_text(statement_url, params)
            
            # Check if still processing
            if "FlexStatementResponse" not in response_text:
                continue
            
            return self._parse_xml(response_text)
        
        raise RuntimeError("Flex Query timed out")
    
    def _parse_xml(self, xml_content: str) -> List[Execution]:
        """Parse Flex Query XML response into executions."""
        root = ET.fromstring(xml_content)
        executions = []
        
        for trade in root.findall(".//Trade"):
            symbol = trade.get("symbol")
            sec_type_str = trade.get("securityType", "STK")
            
            # Map security type
            sec_type_map = {
                "STK": SecurityType.STOCK,
                "OPT": SecurityType.OPTION,
                "FUT": SecurityType.FUTURE,
                "CASH": SecurityType.FOREX,
            }
            sec_type = sec_type_map.get(sec_type_str, SecurityType.STOCK)
            
            # Parse datetime (format: YYYY-MM-DD;HH:MM:SS)
            datetime_str = trade.get("dateTime")
            if ";" in datetime_str:
                exec_time = datetime.strptime(datetime_str, "%Y-%m-%d;%H:%M:%S")
            else:
                exec_time = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S")
            
            # Parse side
            buy_sell = trade.get("buySell")
            side = Side.BUY if buy_sell in ("BUY", "BOT") else Side.SELL
            
            # Parse quantities and prices
            quantity = Decimal(trade.get("quantity"))
            price = Decimal(trade.get("tradePrice"))
            commission = Decimal(trade.get("ibCommission", "0"))
            
            # Option fields
            strike = None
            right = None
            expiry = None
            
            if sec_type == SecurityType.OPTION:
                strike = Decimal(trade.get("strike", "0")) if trade.get("strike") else None
                right = trade.get("putCall")  # 'C' or 'P'
                expiry = trade.get("expiry")
            
            exec = Execution(
                exec_id=trade.get("tradeID"),
                time=exec_time,
                symbol=symbol,
                sec_type=sec_type,
                side=side,
                quantity=abs(quantity),
                price=price,
                commission=abs(commission),
                strike=strike,
                right=right,
                expiry=expiry,
            )
            executions.append(exec)
        
        return executions


class BlotterService:
    """
    Main service for building trade blotters and calculating P&L.
    """
    
    def __init__(self, fetcher: ExecutionFetcher):
        self.fetcher = fetcher
    
    def _group_executions(self, executions: List[Execution]) -> List[Trade]:
        """
        Group executions into trades by contract.
        
        Each unique contract (symbol + strike + expiry + right) gets its own Trade.
        """
        trades_map: Dict[str, Trade] = {}
        
        for exec in executions:
            key = exec.contract_desc
            
            if key not in trades_map:
                trades_map[key] = Trade(
                    symbol=exec.symbol,
                    contract_desc=key,
                    sec_type=exec.sec_type,
                    executions=[],
                )
            
            trades_map[key].executions.append(exec)
        
        # Sort executions within each trade by time
        for trade in trades_map.values():
            trade.executions.sort(key=lambda e: e.time)
        
        return list(trades_map.values())
    
    def build_blotter(self) -> TradeBlotter:
        """
        Fetch executions and build complete trade blotter.
        """
        executions = self.fetcher.fetch_executions()
        trades = self._group_executions(executions)
        
        return TradeBlotter(
            trades=trades,
            as_of=datetime.now(),
        )
    
    def calculate_daily_pnl(self) -> Dict[str, Decimal]:
        """
        Calculate realized P&L for today's closed trades.
        
        Returns:
            Dict with 'realized_pnl', 'commissions', 'net_pnl'
        """
        blotter = self.build_blotter()
        
        return {
            "realized_pnl": blotter.total_realized_pnl,
            "commissions": blotter.total_commissions,
            "net_pnl": blotter.total_realized_pnl,  # Commissions already included in P&L
            "closed_trades": len(blotter.closed_trades),
            "open_trades": len(blotter.open_trades),
        }


def create_blotter_service(
    source: str = "ib",
    host: str = "127.0.0.1",
    port: int = 4001,
    client_id: int = 90,
    flex_token: str = None,
    flex_query_id: str = None,
) -> BlotterService:
    """
    Factory function to create BlotterService with appropriate fetcher.
    
    Args:
        source: "ib" for real-time or "flex" for historical
        host: IB Gateway/TWS host
        port: IB Gateway/TWS port
        client_id: IB client ID
        flex_token: Flex Web Service token (for flex source)
        flex_query_id: Flex Query ID (for flex source)
    """
    if source == "flex":
        if not flex_token or not flex_query_id:
            raise ValueError("flex_token and flex_query_id required for flex source")
        fetcher = FlexQueryFetcher(token=flex_token, query_id=flex_query_id)
    else:
        fetcher = IBFetcher(host=host, port=port, client_id=client_id)
    
    return BlotterService(fetcher=fetcher)
