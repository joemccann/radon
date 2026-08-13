"""Live quote + priced option-chain routes for the in-app assistant.

IB chain endpoints only return strikes. These UW-backed routes supply last
price, bid/ask, IV, and OI so the assistant can name exact debit spreads.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Query

from clients.uw_client import UWClient, UWAPIError

logger = logging.getLogger("radon.assistant_market")

router = APIRouter()

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_OCC_TAIL = re.compile(r"(\d{6})([CP])(\d{8})$", re.IGNORECASE)
_RIGHT_ALIASES = {
    "C": "C",
    "CALL": "C",
    "CALLS": "C",
    "P": "P",
    "PUT": "P",
    "PUTS": "P",
}


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _to_int(value: Any) -> Optional[int]:
    number = _to_float(value)
    if number is None:
        return None
    return int(number)


def normalize_expiry(value: str) -> str:
    digits = re.sub(r"[^0-9]", "", value or "")
    if len(digits) == 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    return (value or "").strip()


def parse_occ_symbol(symbol: str) -> dict[str, Any]:
    compact = re.sub(r"\s+", "", symbol or "").upper()
    match = _OCC_TAIL.search(compact)
    if not match:
        return {}
    yymmdd, right, strike_raw = match.groups()
    year = int(yymmdd[0:2])
    expiry = f"{2000 + year}-{yymmdd[2:4]}-{yymmdd[4:6]}"
    return {
        "expiry": expiry,
        "right": right.upper(),
        "strike": int(strike_raw) / 1000.0,
    }


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def last_from_stock_state(payload: Any) -> Optional[float]:
    data = _unwrap(payload)
    if isinstance(data, list) and data:
        data = data[-1]
    if not isinstance(data, dict):
        return _to_float(data)
    for key in ("last", "close", "regular_market_price", "price"):
        value = _to_float(data.get(key))
        if value is not None and value > 0:
            return value
    return None


def quote_fields(payload: Any) -> dict[str, Optional[float]]:
    data = _unwrap(payload)
    if isinstance(data, list) and data:
        data = data[-1]
    if not isinstance(data, dict):
        last = _to_float(data)
        return {"last": last, "close": last, "bid": None, "ask": None, "volume": None}
    last = last_from_stock_state(payload)
    return {
        "last": last,
        "close": _to_float(data.get("close")) or last,
        "bid": _to_float(data.get("nbbo_bid") or data.get("bid")),
        "ask": _to_float(data.get("nbbo_ask") or data.get("ask")),
        "volume": _to_float(data.get("volume")),
    }


def fetch_yahoo_last(ticker: str) -> Optional[float]:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{ticker}?range=1d&interval=1m"
    )
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return None
    meta = result[0].get("meta") or {}
    return _to_float(meta.get("regularMarketPrice")) or _to_float(meta.get("previousClose"))


def normalize_contract(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    occ = parse_occ_symbol(str(row.get("option_symbol") or row.get("symbol") or ""))
    strike = _to_float(row.get("strike")) or occ.get("strike")
    if strike is None:
        return None
    raw_right = str(row.get("option_type") or row.get("type") or occ.get("right") or "").upper()
    right = _RIGHT_ALIASES.get(raw_right)
    if right is None:
        return None
    bid = _to_float(row.get("nbbo_bid") if "nbbo_bid" in row else row.get("bid"))
    ask = _to_float(row.get("nbbo_ask") if "nbbo_ask" in row else row.get("ask"))
    mid = None
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        mid = round((bid + ask) / 2.0, 4)
    iv = _to_float(row.get("implied_volatility") or row.get("iv"))
    return {
        "strike": strike,
        "right": right,
        "expiry": normalize_expiry(str(row.get("expiry") or row.get("expires") or occ.get("expiry") or "")),
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "iv": iv,
        "oi": _to_int(row.get("open_interest") or row.get("oi")),
        "volume": _to_int(row.get("volume")),
        "delta": _to_float(row.get("delta")),
    }


def compact_contracts(
    rows: list[dict[str, Any]],
    *,
    spot: Optional[float],
    right: Optional[str],
    wings: int,
) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed = normalize_contract(row)
        if parsed is None:
            continue
        if right and parsed["right"] != right:
            continue
        contracts.append(parsed)
    if not contracts:
        return []
    if spot is None or spot <= 0:
        contracts.sort(key=lambda row: row["strike"])
        return contracts[: wings * 2 + 1]
    unique_strikes = sorted({row["strike"] for row in contracts}, key=lambda strike: abs(strike - spot))
    keep = set(unique_strikes[: max(1, wings * 2 + 1)])
    kept = [row for row in contracts if row["strike"] in keep]
    kept.sort(key=lambda row: (row["strike"], row["right"]))
    return kept


def _require_ticker(raw: str) -> str:
    ticker = (raw or "").strip().upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise HTTPException(status_code=400, detail="Invalid ticker")
    return ticker


def _normalize_right(raw: Optional[str]) -> Optional[str]:
    if raw is None or not str(raw).strip():
        return None
    mapped = _RIGHT_ALIASES.get(str(raw).strip().upper())
    if mapped is None:
        raise HTTPException(status_code=400, detail="right must be C or P")
    return mapped


@router.get("/quote/{ticker}")
def quote(ticker: str):
    symbol = _require_ticker(ticker)
    try:
        with UWClient() as client:
            state = client.get_stock_state(symbol)
        fields = quote_fields(state)
        if fields["last"] is not None:
            return {"ticker": symbol, "source": "uw", "missing": False, **fields}
    except UWAPIError as exc:
        logger.warning("UW quote failed for %s: %s", symbol, exc)
    except Exception as exc:  # noqa: BLE001 — degrade to Yahoo, then missing
        logger.warning("UW quote error for %s: %s", symbol, exc)

    yahoo_last = fetch_yahoo_last(symbol)
    if yahoo_last is not None and yahoo_last > 0:
        return {
            "ticker": symbol,
            "source": "yahoo",
            "missing": False,
            "last": yahoo_last,
            "close": yahoo_last,
            "bid": None,
            "ask": None,
            "volume": None,
        }
    return {
        "ticker": symbol,
        "source": None,
        "missing": True,
        "last": None,
        "close": None,
        "bid": None,
        "ask": None,
        "volume": None,
    }


@router.get("/options/uw-chain")
def uw_chain(
    symbol: str,
    expiry: Optional[str] = None,
    right: Optional[str] = None,
    wings: int = Query(default=8, ge=1, le=20),
):
    ticker = _require_ticker(symbol)
    side = _normalize_right(right)
    expiry_iso = normalize_expiry(expiry) if expiry else ""

    try:
        with UWClient() as client:
            state = client.get_stock_state(ticker)
            spot = last_from_stock_state(state)
            if not expiry_iso:
                breakdown = client.get_expiry_breakdown(ticker)
                rows = _unwrap(breakdown)
                expirations = []
                if isinstance(rows, list):
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        date = normalize_expiry(str(row.get("expiry") or row.get("date") or ""))
                        if not date:
                            continue
                        expirations.append(
                            {
                                "expiry": date,
                                "dte": _to_int(row.get("dte") or row.get("days_to_expiry")),
                                "call_volume": _to_int(row.get("call_volume")),
                                "put_volume": _to_int(row.get("put_volume")),
                            }
                        )
                return {
                    "ticker": ticker,
                    "spot": spot,
                    "source": "uw",
                    "expirations": expirations,
                    "contracts": [],
                }

            raw = client.get_option_contracts(
                ticker,
                expiry=expiry_iso,
                **({"option_type": "call" if side == "C" else "put"} if side else {}),
            )
    except UWAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    rows = _unwrap(raw)
    contracts = compact_contracts(
        rows if isinstance(rows, list) else [],
        spot=spot,
        right=side,
        wings=wings,
    )
    return {
        "ticker": ticker,
        "expiry": expiry_iso,
        "spot": spot,
        "source": "uw",
        "count": len(contracts),
        "contracts": contracts,
    }
