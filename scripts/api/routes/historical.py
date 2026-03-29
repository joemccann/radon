"""Historical data endpoints for machine-to-machine access.

Exposes IB historical data operations via REST API so headless clients
(e.g., market-data-warehouse) can fetch bars without a direct IB connection.

Auth: X-API-Key header (scoped in auth middleware) or Clerk JWT.
All endpoints use the "data" pool role for IB access.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("radon.historical")

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ContractSpec(BaseModel):
    sec_type: str = "STK"
    symbol: str
    exchange: str = "SMART"
    currency: str = "USD"
    last_trade_date: Optional[str] = None


class QualifyRequest(BaseModel):
    contracts: List[ContractSpec]


class HeadTimestampRequest(BaseModel):
    contract: ContractSpec
    what_to_show: str = "TRADES"
    use_rth: bool = True


class HistoricalBarsRequest(BaseModel):
    contract: ContractSpec
    end_date_time: str = ""
    duration: str = "1 D"
    bar_size: str = "1 day"
    what_to_show: str = "TRADES"
    use_rth: bool = True


class BarResponse(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_pool():
    """Import ib_pool from server module (avoids circular imports)."""
    from api.server import ib_pool
    if ib_pool is None:
        raise HTTPException(status_code=503, detail="IB pool not initialized")
    return ib_pool


def make_ib_contract(spec: ContractSpec):
    """Reconstruct an ib_insync contract from a JSON spec."""
    from ib_insync import Stock, Future, Index

    if spec.sec_type == "STK":
        return Stock(spec.symbol, spec.exchange, spec.currency)
    elif spec.sec_type == "FUT":
        if not spec.last_trade_date:
            raise HTTPException(status_code=422, detail="last_trade_date required for futures")
        return Future(spec.symbol, spec.last_trade_date, spec.exchange, spec.currency)
    elif spec.sec_type == "IND":
        return Index(spec.symbol, spec.exchange, spec.currency)
    else:
        raise HTTPException(status_code=422, detail=f"Unsupported sec_type: {spec.sec_type}")


def _contract_to_dict(contract) -> dict:
    """Serialize a qualified ib_insync contract to JSON-safe dict."""
    return {
        "conId": contract.conId,
        "symbol": contract.symbol,
        "secType": contract.secType,
        "exchange": contract.exchange,
        "primaryExchange": getattr(contract, "primaryExchange", ""),
        "currency": contract.currency,
        "localSymbol": getattr(contract, "localSymbol", ""),
        "lastTradeDateOrContractMonth": getattr(contract, "lastTradeDateOrContractMonth", ""),
    }


def _bar_date_to_iso(bar_date) -> str:
    """Convert IB bar date to ISO format (YYYY-MM-DD)."""
    if isinstance(bar_date, (date, datetime)):
        return bar_date.isoformat()[:10]
    s = str(bar_date)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _head_timestamp_to_iso(ts) -> str:
    """Convert IB head timestamp to ISO 8601 datetime."""
    if isinstance(ts, datetime):
        return ts.isoformat()
    s = str(ts)
    if "-" in s and len(s) >= 17:
        try:
            dt = datetime.strptime(s, "%Y%m%d-%H:%M:%S")
            return dt.isoformat()
        except ValueError:
            pass
    return s


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/contract/qualify")
async def qualify_contracts(req: QualifyRequest):
    """Qualify one or more contracts against IB."""
    pool = _get_pool()
    contracts = [make_ib_contract(spec) for spec in req.contracts]

    try:
        async with pool.acquire("data") as client:
            qualified = await asyncio.to_thread(
                client.qualify_contracts, *contracts
            )
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {"contracts": [_contract_to_dict(c) for c in qualified]}


@router.post("/historical/head-timestamp")
async def head_timestamp(req: HeadTimestampRequest):
    """Get earliest available data date for a contract."""
    pool = _get_pool()
    contract = make_ib_contract(req.contract)

    try:
        async with pool.acquire("data") as client:
            await asyncio.to_thread(client.qualify_contracts, contract)
            ts = await asyncio.to_thread(
                client.get_head_timestamp,
                contract,
                what_to_show=req.what_to_show,
                use_rth=req.use_rth,
            )
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))

    if not ts:
        return {"timestamp": None}

    return {"timestamp": _head_timestamp_to_iso(ts)}


@router.post("/historical/bars")
async def historical_bars(req: HistoricalBarsRequest):
    """Fetch historical OHLCV bars for a contract."""
    pool = _get_pool()
    contract = make_ib_contract(req.contract)

    try:
        async with pool.acquire("data") as client:
            await asyncio.to_thread(client.qualify_contracts, contract)
            bars = await asyncio.to_thread(
                client.get_historical_data,
                contract,
                end_date_time=req.end_date_time,
                duration=req.duration,
                bar_size=req.bar_size,
                what_to_show=req.what_to_show,
                use_rth=req.use_rth,
            )
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {
        "bars": [
            BarResponse(
                date=_bar_date_to_iso(bar.date),
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=int(bar.volume),
            ).model_dump()
            for bar in bars
        ]
    }
