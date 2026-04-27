"""Data models for trade blotter."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Dict, Any


@dataclass
class InventorySummary:
    """Rolling average-cost summary for a trade's executions."""
    remaining_qty: Decimal
    remaining_basis: Decimal
    realized_qty: Decimal
    realized_basis: Decimal
    realized_pnl: Decimal


class Side(Enum):
    BUY = "BOT"
    SELL = "SLD"


class SecurityType(Enum):
    STOCK = "STK"
    OPTION = "OPT"
    COMBO = "BAG"
    FUTURE = "FUT"
    FOREX = "CASH"


@dataclass
class Execution:
    """Single execution/fill from IB."""
    exec_id: str
    time: datetime
    symbol: str
    sec_type: SecurityType
    side: Side
    quantity: Decimal
    price: Decimal
    commission: Decimal
    # Option-specific fields
    strike: Optional[Decimal] = None
    right: Optional[str] = None  # 'C' or 'P'
    expiry: Optional[str] = None  # YYYYMMDD
    multiplier: int = 100  # Options multiplier
    
    @property
    def contract_desc(self) -> str:
        """Human-readable contract description."""
        if self.sec_type == SecurityType.OPTION:
            return f"{self.symbol} {self.expiry} {self.strike}{self.right}"
        return f"{self.symbol} ({self.sec_type.value})"
    
    @property
    def notional_value(self) -> Decimal:
        """Total notional value of execution (before fees)."""
        if self.sec_type == SecurityType.OPTION:
            return self.quantity * self.price * self.multiplier
        return self.quantity * self.price
    
    @property
    def net_cash_flow(self) -> Decimal:
        """
        Net cash impact of this execution.
        Positive = cash received (sell)
        Negative = cash paid (buy)
        Commission always reduces cash.
        """
        if self.side == Side.BUY:
            return -self.notional_value - self.commission
        else:
            return self.notional_value - self.commission


@dataclass
class Trade:
    """
    A complete trade (round-trip or open position).
    Groups related executions together.
    """
    symbol: str
    contract_desc: str
    sec_type: SecurityType
    executions: List[Execution] = field(default_factory=list)
    
    @property
    def net_quantity(self) -> Decimal:
        """Current position size. 0 = closed."""
        total = Decimal(0)
        for e in self.executions:
            if e.side == Side.BUY:
                total += e.quantity
            else:
                total -= e.quantity
        return total

    @property
    def total_quantity(self) -> Decimal:
        """Total opening-side quantity (position size of the trade)."""
        buys = sum((e.quantity for e in self.executions if e.side == Side.BUY), Decimal(0))
        sells = sum((e.quantity for e in self.executions if e.side == Side.SELL), Decimal(0))
        return max(buys, sells)

    def _inventory_summary(self) -> InventorySummary:
        """Compute remaining basis and realized P&L with rolling average cost.

        Uses average-cost accounting so partial closes realize P&L against the
        average basis/proceeds of the full opening position rather than FIFO.
        """
        position_qty = Decimal(0)  # signed: long positive, short negative
        avg_basis_per_unit = Decimal(0)
        realized_qty = Decimal(0)
        realized_basis = Decimal(0)
        realized_pnl = Decimal(0)

        for e in sorted(self.executions, key=lambda ex: ex.time):
            qty = e.quantity
            if qty <= 0:
                continue

            signed_qty = qty if e.side == Side.BUY else -qty
            same_direction = position_qty == 0 or (position_qty > 0 and signed_qty > 0) or (position_qty < 0 and signed_qty < 0)

            if same_direction:
                if e.side == Side.BUY:
                    opening_total = e.notional_value + e.commission
                else:
                    opening_total = e.notional_value - e.commission
                current_basis = avg_basis_per_unit * abs(position_qty)
                position_qty += signed_qty
                avg_basis_per_unit = (current_basis + opening_total) / abs(position_qty) if position_qty != 0 else Decimal(0)
                continue

            close_qty = min(abs(position_qty), qty)
            if close_qty > 0:
                basis_closed = avg_basis_per_unit * close_qty
                realized_qty += close_qty
                realized_basis += basis_closed

                if position_qty > 0 and e.side == Side.SELL:
                    close_value_per_unit = (e.notional_value - e.commission) / qty
                    realized_pnl += close_value_per_unit * close_qty - basis_closed
                elif position_qty < 0 and e.side == Side.BUY:
                    cover_cost_per_unit = (e.notional_value + e.commission) / qty
                    realized_pnl += basis_closed - cover_cost_per_unit * close_qty

                remaining_qty = abs(position_qty) - close_qty
                position_qty = (Decimal(1) if position_qty > 0 else Decimal(-1)) * remaining_qty if remaining_qty > 0 else Decimal(0)
                if position_qty == 0:
                    avg_basis_per_unit = Decimal(0)

            residual_qty = qty - close_qty
            if residual_qty > 0:
                if e.side == Side.BUY:
                    position_qty = residual_qty
                    avg_basis_per_unit = (e.notional_value + e.commission) / qty
                else:
                    position_qty = -residual_qty
                    avg_basis_per_unit = (e.notional_value - e.commission) / qty

        remaining_basis = avg_basis_per_unit * abs(position_qty)
        return InventorySummary(
            remaining_qty=abs(position_qty),
            remaining_basis=remaining_basis,
            realized_qty=realized_qty,
            realized_basis=realized_basis,
            realized_pnl=realized_pnl,
        )

    @property
    def is_closed(self) -> bool:
        """True if position is fully closed."""
        return self.net_quantity == 0
    
    @property
    def total_commission(self) -> Decimal:
        """Total commissions paid."""
        return sum(e.commission for e in self.executions)
    
    @property
    def total_cash_flow(self) -> Decimal:
        """
        Total cash flow from all executions.
        For closed positions, this equals realized P&L.
        """
        return sum(e.net_cash_flow for e in self.executions)
    
    @property
    def realized_quantity(self) -> Decimal:
        """Quantity already closed/realized within this trade."""
        return self._inventory_summary().realized_qty

    @property
    def realized_cost_basis(self) -> Optional[Decimal]:
        """Average-cost basis allocated to the realized/closed quantity."""
        summary = self._inventory_summary()
        if summary.realized_qty <= 0:
            return None
        return summary.realized_basis

    @property
    def realized_pnl(self) -> Optional[Decimal]:
        """
        Realized P&L for both fully and partially closed positions.
        Returns None only when no quantity has been closed yet.
        """
        summary = self._inventory_summary()
        if summary.realized_qty <= 0:
            return None
        return summary.realized_pnl
    
    @property
    def cost_basis(self) -> Decimal:
        """
        Cost basis.

        - Closed trades: historical buy-side basis (legacy display behavior)
        - Open trades: remaining open basis after any partial closes
        """
        if not self.is_closed:
            return self._inventory_summary().remaining_basis

        total = Decimal(0)
        for e in self.executions:
            if e.side == Side.BUY:
                total += e.notional_value + e.commission
        return total
    
    @property
    def proceeds(self) -> Decimal:
        """
        Total proceeds from sales (net of commissions).
        """
        total = Decimal(0)
        for e in self.executions:
            if e.side == Side.SELL:
                total += e.notional_value - e.commission
        return total
    
    def unrealized_pnl(self, current_price: Decimal) -> Decimal:
        """
        Calculate unrealized P&L given current market price.
        """
        if self.is_closed:
            return Decimal(0)
        
        # Current market value of position
        multiplier = self.executions[0].multiplier if self.executions else 100
        if self.sec_type == SecurityType.OPTION:
            market_value = self.net_quantity * current_price * multiplier
        else:
            market_value = self.net_quantity * current_price
        
        # P&L = market value + cash already received - cost basis
        return market_value + self.total_cash_flow


@dataclass
class Spread:
    """
    Multi-leg spread combining related trades.
    """
    name: str
    symbol: str
    expiry: str
    legs: List[Trade] = field(default_factory=list)
    
    @property
    def is_closed(self) -> bool:
        """All legs must be closed for spread to be closed."""
        return all(leg.is_closed for leg in self.legs)
    
    @property
    def total_commission(self) -> Decimal:
        return sum(leg.total_commission for leg in self.legs)
    
    @property
    def total_cash_flow(self) -> Decimal:
        """Combined cash flow from all legs."""
        return sum(leg.total_cash_flow for leg in self.legs)
    
    @property
    def realized_pnl(self) -> Optional[Decimal]:
        """Combined realized P&L (only if all legs closed)."""
        if not self.is_closed:
            return None
        return self.total_cash_flow
    
    @property
    def net_credit_or_debit(self) -> Decimal:
        """Initial credit (positive) or debit (negative) to open spread."""
        return self.total_cash_flow
    
    def unrealized_pnl(self, leg_prices: Dict[str, Decimal]) -> Decimal:
        """
        Calculate unrealized P&L given current prices for each leg.
        leg_prices: dict mapping contract_desc to current price
        """
        total = Decimal(0)
        for leg in self.legs:
            if leg.contract_desc in leg_prices:
                total += leg.unrealized_pnl(leg_prices[leg.contract_desc])
        return total


@dataclass
class TradeBlotter:
    """Collection of all trades with summary statistics."""
    trades: List[Trade] = field(default_factory=list)
    as_of: datetime = field(default_factory=datetime.now)
    
    @property
    def open_trades(self) -> List[Trade]:
        return [t for t in self.trades if not t.is_closed]
    
    @property
    def closed_trades(self) -> List[Trade]:
        return [t for t in self.trades if t.is_closed]
    
    @property
    def total_realized_pnl(self) -> Decimal:
        """Sum realized P&L across fully and partially closed trades."""
        return sum(t.realized_pnl or Decimal(0) for t in self.trades)
    
    @property
    def total_commissions(self) -> Decimal:
        return sum(t.total_commission for t in self.trades)
    
    def get_trade(self, contract_desc: str) -> Optional[Trade]:
        """Find trade by contract description."""
        for t in self.trades:
            if t.contract_desc == contract_desc:
                return t
        return None
    
    def get_spreads(self) -> List[Spread]:
        """
        Group option trades into spreads by symbol and expiry.
        Returns list of Spread objects combining multi-leg positions.
        """
        from collections import defaultdict
        
        # Group by symbol and expiry
        spread_map: Dict[str, List[Trade]] = defaultdict(list)
        standalone: List[Trade] = []
        
        for trade in self.trades:
            if trade.sec_type == SecurityType.OPTION and trade.executions:
                exec = trade.executions[0]
                if exec.expiry:
                    key = f"{trade.symbol}_{exec.expiry}"
                    spread_map[key].append(trade)
                else:
                    standalone.append(trade)
            else:
                standalone.append(trade)
        
        spreads: List[Spread] = []
        
        for key, legs in spread_map.items():
            if len(legs) >= 2:
                symbol, expiry = key.split("_", 1)
                
                # Determine spread type by analyzing legs
                long_legs = [l for l in legs if l.net_quantity > 0]
                short_legs = [l for l in legs if l.net_quantity < 0]
                
                if long_legs and short_legs:
                    # Vertical spread or risk reversal
                    long_types = set(l.executions[0].right for l in long_legs if l.executions)
                    short_types = set(l.executions[0].right for l in short_legs if l.executions)
                    
                    if long_types == short_types == {"P"}:
                        name = f"Put Spread"
                    elif long_types == short_types == {"C"}:
                        name = f"Call Spread"
                    elif "P" in short_types and "C" in long_types:
                        name = f"Risk Reversal"
                    elif "C" in short_types and "P" in long_types:
                        name = f"Collar"
                    else:
                        name = f"Spread"
                else:
                    name = f"Multi-leg"
                
                spreads.append(Spread(
                    name=f"{symbol} {name}",
                    symbol=symbol,
                    expiry=expiry,
                    legs=legs,
                ))
            else:
                # Single leg option, treat as standalone
                standalone.extend(legs)
        
        return spreads
    
    def get_spread_summary(self) -> Dict[str, any]:
        """Get summary of all spreads with combined P&L."""
        spreads = self.get_spreads()
        
        return {
            "spreads": [
                {
                    "name": s.name,
                    "expiry": s.expiry,
                    "is_closed": s.is_closed,
                    "legs": len(s.legs),
                    "total_commission": s.total_commission,
                    "cash_flow": s.total_cash_flow,
                    "realized_pnl": s.realized_pnl,
                }
                for s in spreads
            ],
            "total_spread_commissions": sum(s.total_commission for s in spreads),
            "total_spread_cash_flow": sum(s.total_cash_flow for s in spreads),
        }
