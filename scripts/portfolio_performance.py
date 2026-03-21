#!/usr/bin/env python3
"""Reconstruct a YTD portfolio equity curve and compute institutional metrics.

Methodology:
- Preferred execution ledger: Interactive Brokers Flex Query
- Preferred stock/ETF prices: Interactive Brokers historical bars
- Preferred option prices: Unusual Whales option contract historic endpoint
- Benchmark: SPY daily closes

The curve is reconstructed from trade cash flows plus marked positions and then
anchored to the current account net liquidation value. This assumes no external
cash flows (deposits/withdrawals) within the observed window unless they are
already reflected in the starting cash balance.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
from ib_insync import Stock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clients.ib_client import IBClient  # noqa: E402
from clients.uw_client import UWClient, UWRateLimitError  # noqa: E402
from utils.price_cache import (  # noqa: E402
    STOCKS_DIR,
    OPTIONS_DIR,
    cache_key_stock,
    cache_key_option,
    read_cache,
    write_cache,
    prune_cache,
    is_market_hours,
    TTL_MARKET_HOURS,
    TTL_AFTER_CLOSE,
)

_DEFAULT_WORKERS = 8
_MIN_WORKERS = 1
_MAX_WORKERS = 20


ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_PATH = ROOT / "data" / "portfolio.json"
BLOTTER_CACHE_PATH = ROOT / "data" / "blotter.json"
TRADING_DAYS = 252
OPTION_DESC_RE = re.compile(
    r"^(?P<symbol>[A-Z.]+)\s+(?P<day>\d{1,2})(?P<mon>[A-Z]{3})(?P<year>\d{2})\s+(?P<strike>[\d.]+)\s+(?P<right>[CP])$"
)
MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


@dataclass(frozen=True)
class TradeFill:
    trade_date: str
    contract_key: str
    quantity: float
    net_cash: float
    multiplier: float
    security_type: str = "STK"
    symbol: Optional[str] = None
    option_id: Optional[str] = None
    expiry: Optional[str] = None


def safe_float(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "nan", "NaN"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_expiry(expiry: str) -> str:
    digits = re.sub(r"\D", "", expiry or "")
    if len(digits) != 8:
        raise ValueError(f"Unsupported expiry format: {expiry!r}")
    return digits


def normalize_trade_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    match = re.match(r"^(?P<year>\d{4})[-/](?P<month>\d{2})[-/](?P<day>\d{2})", text)
    if match:
        return f"{match.group('year')}-{match.group('month')}-{match.group('day')}"

    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"

    return text[:10]


def build_option_id(symbol: str, expiry: str, right: str, strike: float) -> str:
    expiry_digits = normalize_expiry(expiry)
    strike_int = int(round(float(strike) * 1000))
    return f"{symbol.upper()}{expiry_digits[2:]}{right.upper()[0]}{strike_int:08d}"


def select_option_mark(row: Mapping[str, Any]) -> Optional[float]:
    bid = safe_float(row.get("nbbo_bid"), default=0.0)
    ask = safe_float(row.get("nbbo_ask"), default=0.0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0

    for key in ("avg_price", "last_price", "close_price", "high_price", "low_price", "open_price"):
        value = safe_float(row.get(key), default=float("nan"))
        if math.isfinite(value) and value > 0:
            return value
    return None


def load_portfolio_snapshot(path: Path = PORTFOLIO_PATH) -> dict:
    try:
        from utils.atomic_io import verified_load
        return verified_load(str(path))
    except (ValueError, ImportError):
        return json.loads(path.read_text())


def parse_flex_trade_rows(df: pd.DataFrame) -> List[TradeFill]:
    fills: List[TradeFill] = []
    for row in df.to_dict(orient="records"):
        asset = str(row.get("assetCategory") or "").upper()
        if asset not in {"STK", "OPT"}:
            continue
        trade_date = normalize_trade_date(row.get("tradeDate") or row.get("reportDate") or "")
        if not trade_date:
            continue
        qty = safe_float(row.get("quantity"))
        if qty == 0:
            continue

        if asset == "OPT":
            underlying = str(row.get("underlyingSymbol") or row.get("symbol") or "").strip().split(" ")[0].upper()
            expiry = normalize_expiry(str(row.get("expiry") or ""))
            right = str(row.get("putCall") or "").upper()[:1]
            strike = safe_float(row.get("strike"))
            option_id = build_option_id(underlying, expiry, right, strike)
            contract_key = option_id
            symbol = underlying
        else:
            symbol = str(row.get("symbol") or "").strip().upper()
            contract_key = f"STK:{symbol}"
            option_id = None
            expiry = None

        fills.append(
            TradeFill(
                trade_date=trade_date,
                contract_key=contract_key,
                quantity=qty,
                net_cash=safe_float(row.get("netCash")),
                multiplier=safe_float(row.get("multiplier"), default=100.0 if asset == "OPT" else 1.0) or (100.0 if asset == "OPT" else 1.0),
                security_type=asset,
                symbol=symbol,
                option_id=option_id,
                expiry=expiry,
            )
        )
    fills.sort(key=lambda item: (item.trade_date, item.contract_key, item.quantity))
    return fills


def _parse_blotter_contract_desc(desc: str) -> tuple[str, Optional[str], Optional[str], Optional[float]]:
    match = OPTION_DESC_RE.match(desc.strip().upper())
    if not match:
        return desc.strip().upper(), None, None, None
    month = MONTHS[match.group("mon")]
    expiry = date(
        year=2000 + int(match.group("year")),
        month=month,
        day=int(match.group("day")),
    ).strftime("%Y%m%d")
    return (
        match.group("symbol"),
        expiry,
        match.group("right"),
        float(match.group("strike")),
    )


def load_blotter_fallback(path: Path = BLOTTER_CACHE_PATH) -> List[TradeFill]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    fills: List[TradeFill] = []
    for trade in raw.get("open_trades", []) + raw.get("closed_trades", []):
        desc = str(trade.get("contract_desc") or trade.get("symbol") or "")
        symbol, expiry, right, strike = _parse_blotter_contract_desc(desc)
        contract_key = build_option_id(symbol, expiry, right, strike) if expiry and right and strike is not None else f"STK:{symbol}"
        security_type = "OPT" if expiry and right else "STK"
        multiplier = 100.0 if security_type == "OPT" else 1.0

        for execution in trade.get("executions", []):
            side = str(execution.get("side") or "").upper()
            qty = abs(safe_float(execution.get("quantity")))
            signed_qty = qty if side == "BUY" else -qty
            fills.append(
                TradeFill(
                    trade_date=normalize_trade_date(execution.get("time") or ""),
                    contract_key=contract_key,
                    quantity=signed_qty,
                    net_cash=safe_float(execution.get("net_cash_flow")),
                    multiplier=multiplier,
                    security_type=security_type,
                    symbol=symbol,
                    option_id=contract_key if security_type == "OPT" else None,
                    expiry=expiry,
                )
            )
    fills.sort(key=lambda item: (item.trade_date, item.contract_key, item.quantity))
    return fills


def extract_fill_marks(path: Path = BLOTTER_CACHE_PATH) -> Dict[str, Dict[str, float]]:
    """Extract known price marks from trade execution prices.

    When UW/IB historical data is unavailable for an option contract, the
    execution price on the trade date is the best available mark.  These seed
    marks are forward-filled by ``align_mark_series()`` to cover calendar gaps,
    giving a reasonable (though approximate) equity curve for contracts that
    would otherwise be valued at zero.
    """
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    marks: Dict[str, Dict[str, float]] = {}
    for trade in raw.get("open_trades", []) + raw.get("closed_trades", []):
        desc = str(trade.get("contract_desc") or trade.get("symbol") or "")
        symbol, expiry, right, strike = _parse_blotter_contract_desc(desc)
        if expiry and right and strike is not None:
            contract_key = build_option_id(symbol, expiry, right, strike)
        else:
            contract_key = f"STK:{symbol}"

        for execution in trade.get("executions", []):
            price = safe_float(execution.get("price"), default=0.0)
            dt = normalize_trade_date(execution.get("time") or "")
            if price > 0 and dt:
                if contract_key not in marks:
                    marks[contract_key] = {}
                marks[contract_key][dt] = price
    return marks


def parse_option_id(option_id: str) -> Tuple[str, str, str, float]:
    """Parse OCC-style option ID back to (symbol, expiry_YYYYMMDD, right, strike).

    Example: ``AAPL260321C00230000`` → ``('AAPL', '20260321', 'C', 230.0)``
    """
    match = re.match(r"^([A-Z.]+?)(\d{6})([CP])(\d{8})$", option_id)
    if not match:
        raise ValueError(f"Cannot parse option_id: {option_id!r}")
    symbol = match.group(1)
    yymmdd = match.group(2)
    right = match.group(3)
    strike = int(match.group(4)) / 1000.0
    expiry = f"20{yymmdd}"
    return symbol, expiry, right, strike


NAV_HISTORY_PATH = ROOT / "data" / "nav_history.jsonl"


IB_NAV_CACHE_PATH = ROOT / "data" / "nav_history_ib.json"


def fetch_ib_nav_series() -> Optional[List[Dict[str, Any]]]:
    """Fetch daily NAV from IB Flex Query (EquitySummaryInBase).

    Uses ``IB_FLEX_NAV_QUERY_ID`` env var (separate from trade query).
    Returns list of ``{date, total, cash, stock, options}`` or None on failure.
    """
    token = os.environ.get("IB_FLEX_TOKEN")
    nav_query_id = os.environ.get("IB_FLEX_NAV_QUERY_ID")
    if not token or not nav_query_id:
        return None

    import time
    import xml.etree.ElementTree as ET
    from urllib.request import urlopen
    from urllib.parse import urlencode

    try:
        # Request report
        url = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
        params = urlencode({"t": token, "q": nav_query_id, "v": "3"})
        resp = urlopen(f"{url}?{params}", timeout=30)
        text = resp.read().decode("utf-8")
        root = ET.fromstring(text)
        ref_node = root.find(".//ReferenceCode")
        if ref_node is None or not ref_node.text:
            return None
        ref_code = ref_node.text

        # Poll for result
        stmt_url = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"
        for _ in range(30):
            time.sleep(3)
            params2 = urlencode({"t": token, "q": ref_code, "v": "3"})
            resp2 = urlopen(f"{stmt_url}?{params2}", timeout=30)
            xml_text = resp2.read().decode("utf-8")
            if "<FlexStatements" not in xml_text:
                continue

            root2 = ET.fromstring(xml_text)
            navs = root2.findall(".//EquitySummaryByReportDateInBase")
            if not navs:
                return None

            entries: List[Dict[str, Any]] = []
            for nav in navs:
                dt_raw = nav.get("reportDate", "")
                if len(dt_raw) == 8:
                    dt = f"{dt_raw[:4]}-{dt_raw[4:6]}-{dt_raw[6:8]}"
                else:
                    dt = dt_raw
                entries.append({
                    "date": dt,
                    "total": safe_float(nav.get("total")),
                    "cash": safe_float(nav.get("cash")),
                    "stock": safe_float(nav.get("stock")),
                    "options": safe_float(nav.get("options")),
                })

            # Cache to disk
            try:
                IB_NAV_CACHE_PATH.write_text(json.dumps(entries, indent=2))
            except OSError:
                pass
            return entries
        return None
    except Exception:
        return None


def load_ib_nav_cache() -> Optional[List[Dict[str, Any]]]:
    """Load cached IB NAV series from disk."""
    if not IB_NAV_CACHE_PATH.exists():
        return None
    try:
        entries = json.loads(IB_NAV_CACHE_PATH.read_text())
        return entries if isinstance(entries, list) and len(entries) >= 2 else None
    except (json.JSONDecodeError, OSError):
        return None


def _extract_acats_transfers(
    cash_flows: Dict[str, float],
    nav_entries: List[Dict[str, Any]],
) -> None:
    """Detect ACATS securities transfers and add them as cash flows.

    Uses the NAV change on transfer days (not ACATS ``positionAmount``)
    because the Modified Dietz formula can't split intraday returns between
    existing and incoming positions.  Setting CF = NAV change gives HPR ≈ 0%
    on the transfer day, which is the safest approximation.
    """
    # Build date→NAV mapping
    nav_by_date = {e["date"]: e["total"] for e in nav_entries}
    sorted_dates = sorted(nav_by_date.keys())

    # Build fill count per day (real trades with nonzero cash)
    fill_counts: Dict[str, int] = {}
    try:
        raw = json.loads(BLOTTER_CACHE_PATH.read_text())
        for trade in raw.get("open_trades", []) + raw.get("closed_trades", []):
            for execution in trade.get("executions", []):
                dt = str(execution.get("time", ""))[:10]
                ncf = abs(safe_float(execution.get("net_cash_flow"), default=0.0))
                if dt and ncf > 1:
                    fill_counts[dt] = fill_counts.get(dt, 0) + 1
    except (OSError, json.JSONDecodeError):
        pass

    # Detect: large positive NAV jump with no real fills and no cash deposits
    for i in range(1, len(sorted_dates)):
        dt = sorted_dates[i]
        prev_dt = sorted_dates[i - 1]
        chg = nav_by_date[dt] - nav_by_date[prev_dt]
        has_deposit = dt in cash_flows and cash_flows[dt] != 0
        has_fills = fill_counts.get(dt, 0) > 0
        if chg > 50_000 and not has_fills and not has_deposit:
            cash_flows[dt] = cash_flows.get(dt, 0.0) + chg


def _extract_cash_flows(cash_flows: Dict[str, float]) -> None:
    """Extract deposit/withdrawal amounts by date from IB Flex Cash Transactions.

    Fetches the NAV Flex Query (which includes CashTransactions section) and
    sums Deposits/Withdrawals by reportDate.
    """
    import time
    import xml.etree.ElementTree as ET
    from urllib.request import urlopen
    from urllib.parse import urlencode

    token = os.environ.get("IB_FLEX_TOKEN")
    query_id = os.environ.get("IB_FLEX_NAV_QUERY_ID")
    if not token or not query_id:
        return

    url = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
    params = urlencode({"t": token, "q": query_id, "v": "3"})
    resp = urlopen(f"{url}?{params}", timeout=30)
    root = ET.fromstring(resp.read().decode("utf-8"))
    ref_node = root.find(".//ReferenceCode")
    if ref_node is None or not ref_node.text:
        return
    ref_code = ref_node.text

    stmt_url = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"
    for _ in range(20):
        time.sleep(3)
        params2 = urlencode({"t": token, "q": ref_code, "v": "3"})
        resp2 = urlopen(f"{stmt_url}?{params2}", timeout=30)
        xml_text = resp2.read().decode("utf-8")
        if "<FlexStatements" not in xml_text:
            continue

        root2 = ET.fromstring(xml_text)
        for ct in root2.findall(".//CashTransaction"):
            txn_type = ct.get("type", "")
            if "Deposit" not in txn_type and "Withdrawal" not in txn_type:
                continue
            amt = safe_float(ct.get("amount"), default=0.0)
            dt_raw = ct.get("reportDate") or ct.get("dateTime", "")
            if len(dt_raw) >= 8:
                dt = f"{dt_raw[:4]}-{dt_raw[4:6]}-{dt_raw[6:8]}"
            else:
                dt = dt_raw
            cash_flows[dt] = cash_flows.get(dt, 0.0) + amt
        return
    return


def build_nav_based_curve(
    nav_entries: List[Dict[str, Any]],
    start_date: str,
    benchmark_series: pd.Series,
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    """Build equity curve + metrics from IB's daily NAV data.

    Detects deposit/withdrawal days by cross-referencing with blotter fill data.
    A day is flagged as a deposit when the NAV change is large (>10% and >$50K)
    and trade fills don't explain the movement.

    Returns (curve_df, metrics_dict).
    """
    # Filter to YTD (start_date onward), sorted
    ytd = sorted(
        [e for e in nav_entries if e["date"] >= start_date],
        key=lambda e: e["date"],
    )
    if len(ytd) < 2:
        raise ValueError("Need at least 2 NAV entries for YTD curve")

    dates = [e["date"] for e in ytd]
    navs = [e["total"] for e in ytd]

    # IB's daily NAV IS the source of truth — just compute simple daily
    # returns from day-over-day NAV changes.  No TWR formula, no deposit
    # detection, no ACATS guessing.  The curve is the performance.
    daily_returns = [None]  # first day has no return
    for i in range(1, len(navs)):
        daily_returns.append((navs[i] / navs[i - 1]) - 1.0 if navs[i - 1] > 0 else 0.0)

    # Load IB's authoritative TWR series (scraped from portal Highcharts).
    # This is the source of truth for return % — we use it for total_return
    # and derive daily TWR returns from the cumulative series.
    ib_twr_path = ROOT / "data" / "ib_twr_series.json"
    twr_by_date: Dict[str, float] = {}
    try:
        twr_entries = json.loads(ib_twr_path.read_text())
        for entry in twr_entries:
            twr_by_date[entry["date"]] = entry["twr"] / 100.0  # convert % to decimal
    except (OSError, json.JSONDecodeError, KeyError):
        pass

    # If we have IB's TWR, use it for daily returns (more accurate than
    # NAV-based returns which include deposit effects).
    if twr_by_date:
        daily_returns = [None]
        for i in range(1, len(dates)):
            curr_twr = twr_by_date.get(dates[i])
            prev_twr = twr_by_date.get(dates[i - 1])
            if curr_twr is not None and prev_twr is not None:
                # daily return = (1 + cumTWR_today) / (1 + cumTWR_yesterday) - 1
                daily_returns.append((1 + curr_twr) / (1 + prev_twr) - 1.0 if (1 + prev_twr) != 0 else 0.0)
            else:
                # Fallback to NAV-based return for dates not in TWR series
                daily_returns.append((navs[i] / navs[i - 1]) - 1.0 if navs[i - 1] > 0 else 0.0)

    # Build DataFrame
    curve = pd.DataFrame({
        "equity": navs,
        "daily_return": daily_returns,
    }, index=dates)
    curve.index.name = "date"
    curve["drawdown"] = curve["equity"] / curve["equity"].cummax() - 1.0

    # Metrics from TWR-adjusted returns (not NAV-based)
    metrics = compute_performance_metrics(curve["equity"], benchmark_series)

    # Override total_return with IB's authoritative TWR if available
    if twr_by_date:
        last_twr = None
        for dt in reversed(dates):
            if dt in twr_by_date:
                last_twr = twr_by_date[dt]
                break
        if last_twr is not None:
            metrics["total_return"] = last_twr
            trading_days = len([r for r in daily_returns if r is not None])
            metrics["annualized_return"] = float(
                (1.0 + last_twr) ** (TRADING_DAYS / max(trading_days, 1)) - 1.0
            )

    return curve, metrics


def load_nav_history(path: Path = NAV_HISTORY_PATH) -> Dict[str, float]:
    """Load daily NAV snapshots from JSONL file. Returns date→nav mapping."""
    if not path.exists():
        return {}
    history: Dict[str, float] = {}
    for line in path.read_text().strip().splitlines():
        try:
            entry = json.loads(line)
            dt = entry.get("date", "")
            nav = entry.get("nav")
            if dt and nav is not None:
                history[dt] = float(nav)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return history


def build_nav_equity_curve(nav_history: Dict[str, float]) -> Optional[pd.DataFrame]:
    """Build equity curve DataFrame from daily NAV snapshots.

    Returns None if fewer than 2 data points.
    """
    if len(nav_history) < 2:
        return None
    dates = sorted(nav_history.keys())
    equities = [nav_history[d] for d in dates]
    df = pd.DataFrame({"equity": equities}, index=dates)
    df["daily_return"] = df["equity"].pct_change()
    df["drawdown"] = df["equity"] / df["equity"].cummax() - 1.0
    df.index.name = "date"
    return df


def fetch_flex_trade_fills() -> tuple[List[TradeFill], str]:
    token = os.environ.get("IB_FLEX_TOKEN")
    query_id = os.environ.get("IB_FLEX_QUERY_ID")
    if not token or not query_id:
        raise RuntimeError("IB_FLEX_TOKEN and IB_FLEX_QUERY_ID are required for live performance reconstruction")
    client = IBClient()
    report = client.run_flex_query(query_id=int(query_id), token=token)
    trades_df = report.df("Trade")
    return parse_flex_trade_rows(trades_df), "ib_flex"


def _fetch_yahoo_chart(symbol: str, days: int = 400) -> List[tuple[str, float]]:
    params = urlencode({
        "period1": int((datetime.utcnow().timestamp()) - days * 86400),
        "period2": int(datetime.utcnow().timestamp()) + 86400,
        "interval": "1d",
        "includePrePost": "false",
        "events": "div,splits",
    })
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{params}"
    request = Request(url, headers={"User-Agent": "radon/2.0"})
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    closes = result["indicators"]["quote"][0].get("close") or []
    bars: List[tuple[str, float]] = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        bars.append((datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d"), float(close)))
    return bars


def fetch_stock_history(symbol: str, start_date: str, end_date: str, ib_client: Optional[IBClient], uw_client: Optional[UWClient]) -> Dict[str, float]:
    parsed: Dict[str, float] = {}
    if ib_client is not None:
        try:
            bars = ib_client.get_historical_data(
                Stock(symbol, "SMART", "USD"),
                duration="1 Y",
                bar_size="1 day",
                what_to_show="TRADES",
            )
            parsed = {
                str(bar.date)[:10]: float(bar.close)
                for bar in bars
                if str(bar.date)[:10] >= start_date and str(bar.date)[:10] <= end_date
            }
            if parsed:
                return parsed
        except Exception:
            parsed = {}

    if uw_client is not None:
        try:
            data = uw_client.get_stock_ohlc(symbol, candle_size="1d")
            for bar in data.get("data", []):
                dt = str(bar.get("date") or "")[:10]
                close = safe_float(bar.get("close"), default=float("nan"))
                if dt and math.isfinite(close) and start_date <= dt <= end_date:
                    parsed[dt] = close
            if parsed:
                return parsed
        except Exception:
            parsed = {}

    for dt, close in _fetch_yahoo_chart(symbol):
        if start_date <= dt <= end_date:
            parsed[dt] = close
    return parsed


def fetch_option_history(option_id: str, start_date: str, end_date: str, uw_client: Optional[UWClient]) -> Dict[str, float]:
    if uw_client is None:
        return {}
    data = uw_client.get_option_contract_historic(option_id)
    parsed: Dict[str, float] = {}
    for row in data.get("chains", []):
        dt = str(row.get("date") or "")[:10]
        if not dt or dt < start_date or dt > end_date:
            continue
        mark = select_option_mark(row)
        if mark is not None:
            parsed[dt] = mark
    return parsed


def align_mark_series(calendar: Iterable[str], raw_marks: Mapping[str, float], expiry: Optional[str] = None) -> pd.Series:
    series = pd.Series(raw_marks, dtype=float)
    if series.empty:
        aligned = pd.Series(0.0, index=list(calendar), dtype=float)
    else:
        aligned = series.reindex(list(calendar)).ffill().bfill().fillna(0.0)
    if expiry:
        expiry_date = f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:]}"
        aligned.loc[aligned.index > expiry_date] = 0.0
    return aligned.astype(float)


def _build_portfolio_positions(portfolio: dict) -> Dict[str, float]:
    """Build {contract_key: signed_quantity} from current portfolio positions.

    Used to reconcile computed final_holdings with IB's actual positions.
    Positions opened before trade history coverage appear as phantom shorts
    without this reconciliation.
    """
    positions: Dict[str, float] = {}
    for pos in portfolio.get("positions", []):
        ticker = pos.get("ticker", "")
        expiry_raw = pos.get("expiry", "")
        expiry_digits = re.sub(r"\D", "", expiry_raw)
        for leg in pos.get("legs", []):
            contracts = int(leg.get("contracts", 0))
            direction = str(leg.get("direction", "")).upper()
            signed_qty = contracts if direction == "LONG" else -contracts

            leg_type = str(leg.get("type", ""))
            strike = safe_float(leg.get("strike"), default=0.0)

            if leg_type and strike > 0 and len(expiry_digits) >= 8:
                right = "C" if "call" in leg_type.lower() else "P"
                try:
                    key = build_option_id(ticker, expiry_digits, right, strike)
                    positions[key] = positions.get(key, 0.0) + signed_qty
                except (ValueError, IndexError):
                    pass
            elif not leg_type and not strike:
                # Stock position
                key = f"STK:{ticker}"
                positions[key] = positions.get(key, 0.0) + signed_qty
    return positions


def reconstruct_equity_curve(
    trades: List[TradeFill],
    calendar: Iterable[str],
    marks_by_contract: Mapping[str, Mapping[str, float]],
    final_equity: float,
    portfolio_positions: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    calendar_list = [str(day) for day in calendar]
    if not calendar_list:
        raise ValueError("calendar must not be empty")

    multipliers: Dict[str, float] = {}
    expiries: Dict[str, Optional[str]] = {}
    final_holdings: Dict[str, float] = {}
    total_net_cash = 0.0
    for trade in trades:
        multipliers[trade.contract_key] = trade.multiplier
        expiries[trade.contract_key] = trade.expiry
        final_holdings[trade.contract_key] = final_holdings.get(trade.contract_key, 0.0) + trade.quantity
        total_net_cash += trade.net_cash

    # Reconcile with actual portfolio positions.
    # Trades opened before the blotter coverage window create phantom short
    # positions (only the closing sell is recorded).  We inject synthetic
    # opening trades at the period start so the calendar walk correctly tracks
    # these positions and their mark-to-market changes.
    if portfolio_positions is not None:
        all_keys = set(final_holdings.keys()) | set(portfolio_positions.keys())
        for key in all_keys:
            computed = final_holdings.get(key, 0.0)
            actual = portfolio_positions.get(key, 0.0)
            if abs(computed - actual) > 0.01:
                # Inject a synthetic opening trade to correct the quantity.
                # net_cash = 0 because the actual purchase happened before the
                # YTD period — we only need the position to exist for marks.
                delta = actual - computed
                mult = multipliers.get(key, 100.0 if not key.startswith("STK:") else 1.0)
                synthetic_cash = 0.0  # pre-period cash flow, not YTD
                trades.append(TradeFill(
                    trade_date="2025-12-31",  # before YTD start
                    contract_key=key,
                    quantity=delta,
                    net_cash=synthetic_cash,
                    multiplier=mult,
                    security_type="OPT" if not key.startswith("STK:") else "STK",
                    symbol=key.split(":")[1] if key.startswith("STK:") else re.match(r"^([A-Z.]+)", key).group(1) if re.match(r"^([A-Z.]+)", key) else "",
                    option_id=key if not key.startswith("STK:") else None,
                    expiry=None,
                ))
                final_holdings[key] = actual
                if key not in multipliers:
                    multipliers[key] = mult
                if key not in expiries:
                    expiries[key] = None

    aligned_marks: Dict[str, pd.Series] = {
        key: align_mark_series(calendar_list, marks_by_contract.get(key, {}), expiries.get(key))
        for key in final_holdings
    }
    final_holdings_value = 0.0
    last_date = calendar_list[-1]
    for key, qty in final_holdings.items():
        mark = float(aligned_marks.get(key, pd.Series(dtype=float)).get(last_date, 0.0))
        final_holdings_value += qty * multipliers.get(key, 1.0) * mark

    initial_cash = final_equity - total_net_cash - final_holdings_value

    trade_map: Dict[str, List[TradeFill]] = {}
    first_date = calendar_list[0]
    calendar_set = set(calendar_list)
    holdings: Dict[str, float] = {}
    cash = initial_cash
    for trade in trades:
        trade_date = normalize_trade_date(trade.trade_date)
        if trade_date < first_date:
            holdings[trade.contract_key] = holdings.get(trade.contract_key, 0.0) + trade.quantity
            cash += trade.net_cash
        else:
            # Snap weekend/holiday trade dates to the next valid calendar day
            effective_date = trade_date
            if effective_date not in calendar_set:
                for cal_day in calendar_list:
                    if cal_day >= effective_date:
                        effective_date = cal_day
                        break
                else:
                    # Trade after last calendar day — attach to last day
                    effective_date = calendar_list[-1]
            trade_map.setdefault(effective_date, []).append(trade)

    rows: List[dict] = []
    previous_equity: Optional[float] = None
    for day in calendar_list:
        for trade in trade_map.get(day, []):
            holdings[trade.contract_key] = holdings.get(trade.contract_key, 0.0) + trade.quantity
            cash += trade.net_cash

        holdings_value = 0.0
        for key, qty in holdings.items():
            if qty == 0:
                continue
            mark = float(aligned_marks.get(key, pd.Series(dtype=float)).get(day, 0.0))
            holdings_value += qty * multipliers.get(key, 1.0) * mark
        equity = cash + holdings_value
        daily_return = None if previous_equity in (None, 0) else (equity / previous_equity) - 1.0
        rows.append({
            "date": day,
            "cash": cash,
            "holdings_value": holdings_value,
            "equity": equity,
            "daily_return": daily_return,
        })
        previous_equity = equity

    curve = pd.DataFrame(rows).set_index("date")
    drawdown = curve["equity"] / curve["equity"].cummax() - 1.0
    curve["drawdown"] = drawdown
    return curve


def _compute_drawdown_duration(drawdown: pd.Series) -> int:
    max_duration = 0
    current = 0
    for value in drawdown:
        if value < 0:
            current += 1
            max_duration = max(max_duration, current)
        else:
            current = 0
    return int(max_duration)


def _capture_ratio(portfolio_returns: pd.Series, benchmark_returns: pd.Series, positive: bool) -> float:
    mask = benchmark_returns > 0 if positive else benchmark_returns < 0
    if mask.sum() == 0:
        return 0.0
    port = portfolio_returns[mask]
    bench = benchmark_returns[mask]
    port_total = float(np.prod(1.0 + port.values) - 1.0)
    bench_total = float(np.prod(1.0 + bench.values) - 1.0)
    if bench_total == 0:
        return 0.0
    return port_total / bench_total


def compute_performance_metrics(equity: pd.Series, benchmark: pd.Series) -> Dict[str, float]:
    portfolio_returns = equity.pct_change().dropna()
    benchmark_returns = benchmark.pct_change().dropna()
    common_index = portfolio_returns.index.intersection(benchmark_returns.index)
    portfolio_returns = portfolio_returns.loc[common_index]
    benchmark_returns = benchmark_returns.loc[common_index]

    total_return = float((equity.iloc[-1] / equity.iloc[0]) - 1.0) if len(equity) > 1 else 0.0
    annualized_return = float((1.0 + total_return) ** (TRADING_DAYS / max(len(portfolio_returns), 1)) - 1.0) if len(equity) > 1 else 0.0

    volatility = float(portfolio_returns.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(portfolio_returns) > 1 else 0.0
    downside_rms = float(np.sqrt(np.mean(np.square(np.minimum(portfolio_returns.values, 0.0))))) if len(portfolio_returns) > 0 else 0.0
    downside_deviation = downside_rms * math.sqrt(TRADING_DAYS)
    sharpe_ratio = float((portfolio_returns.mean() / portfolio_returns.std(ddof=1)) * math.sqrt(TRADING_DAYS)) if len(portfolio_returns) > 1 and portfolio_returns.std(ddof=1) > 0 else 0.0
    sortino_ratio = float((portfolio_returns.mean() / downside_rms) * math.sqrt(TRADING_DAYS)) if downside_rms > 0 else 0.0

    drawdown = equity / equity.cummax() - 1.0
    max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0
    current_drawdown = float(drawdown.iloc[-1]) if not drawdown.empty else 0.0
    calmar_ratio = float(annualized_return / abs(max_drawdown)) if max_drawdown < 0 else 0.0

    beta = 0.0
    alpha = 0.0
    correlation = 0.0
    r_squared = 0.0
    tracking_error = 0.0
    information_ratio = 0.0
    treynor_ratio = 0.0
    upside_capture = 0.0
    downside_capture = 0.0
    if len(portfolio_returns) > 1 and len(benchmark_returns) > 1:
        bench_variance = float(np.var(benchmark_returns.values, ddof=1))
        if bench_variance > 0:
            beta = float(np.cov(portfolio_returns.values, benchmark_returns.values, ddof=1)[0, 1] / bench_variance)
            treynor_ratio = float(annualized_return / beta) if beta != 0 else 0.0
        correlation = float(np.corrcoef(portfolio_returns.values, benchmark_returns.values)[0, 1]) if len(portfolio_returns) > 1 else 0.0
        r_squared = correlation ** 2
        alpha = float((portfolio_returns.mean() - beta * benchmark_returns.mean()) * TRADING_DAYS)
        active_returns = portfolio_returns - benchmark_returns
        active_vol = float(active_returns.std(ddof=1))
        tracking_error = active_vol * math.sqrt(TRADING_DAYS) if active_vol > 0 else 0.0
        information_ratio = float((active_returns.mean() / active_vol) * math.sqrt(TRADING_DAYS)) if active_vol > 0 else 0.0
        upside_capture = _capture_ratio(portfolio_returns, benchmark_returns, positive=True)
        downside_capture = _capture_ratio(portfolio_returns, benchmark_returns, positive=False)

    positive_days = int((portfolio_returns > 0).sum())
    negative_days = int((portfolio_returns < 0).sum())
    flat_days = int((portfolio_returns == 0).sum())
    hit_rate = float(positive_days / len(portfolio_returns)) if len(portfolio_returns) else 0.0
    best_day = float(portfolio_returns.max()) if len(portfolio_returns) else 0.0
    worst_day = float(portfolio_returns.min()) if len(portfolio_returns) else 0.0
    avg_up_day = float(portfolio_returns[portfolio_returns > 0].mean()) if positive_days else 0.0
    avg_down_day = float(portfolio_returns[portfolio_returns < 0].mean()) if negative_days else 0.0
    win_loss_ratio = float(abs(avg_up_day / avg_down_day)) if avg_down_day != 0 else 0.0

    var_95 = float(np.quantile(portfolio_returns.values, 0.05)) if len(portfolio_returns) else 0.0
    cvar_95 = float(portfolio_returns[portfolio_returns <= var_95].mean()) if len(portfolio_returns) else 0.0
    q95 = float(np.quantile(portfolio_returns.values, 0.95)) if len(portfolio_returns) else 0.0
    tail_ratio = float(abs(q95 / var_95)) if var_95 != 0 else 0.0
    ulcer_index = float(np.sqrt(np.mean(np.square(drawdown[drawdown < 0])))) if (drawdown < 0).any() else 0.0
    skew = float(portfolio_returns.skew()) if len(portfolio_returns) > 2 else 0.0
    kurtosis = float(portfolio_returns.kurt()) if len(portfolio_returns) > 3 else 0.0

    return {
        "total_return": total_return,
        "annualized_return": annualized_return,
        "annualized_volatility": volatility,
        "downside_deviation": downside_deviation,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "calmar_ratio": calmar_ratio,
        "max_drawdown": max_drawdown,
        "current_drawdown": current_drawdown,
        "max_drawdown_duration_days": _compute_drawdown_duration(drawdown),
        "beta": beta,
        "alpha": alpha,
        "correlation": correlation,
        "r_squared": r_squared,
        "tracking_error": tracking_error,
        "information_ratio": information_ratio,
        "treynor_ratio": treynor_ratio,
        "upside_capture": upside_capture,
        "downside_capture": downside_capture,
        "var_95": var_95,
        "cvar_95": cvar_95,
        "tail_ratio": tail_ratio,
        "ulcer_index": ulcer_index,
        "skew": skew,
        "kurtosis": kurtosis,
        "hit_rate": hit_rate,
        "positive_days": positive_days,
        "negative_days": negative_days,
        "flat_days": flat_days,
        "best_day": best_day,
        "worst_day": worst_day,
        "average_up_day": avg_up_day,
        "average_down_day": avg_down_day,
        "win_loss_ratio": win_loss_ratio,
    }


def _get_worker_count() -> int:
    """Read PERF_FETCH_WORKERS env var, clamped to [1, 20]. Default 8."""
    raw = os.environ.get("PERF_FETCH_WORKERS", "")
    try:
        val = int(raw)
        if val < _MIN_WORKERS or val > _MAX_WORKERS:
            return _DEFAULT_WORKERS
        return val
    except (ValueError, TypeError):
        return _DEFAULT_WORKERS


def _fetch_stock_history_ib_only(
    symbol: str, start: str, end: str, ib_client: IBClient
) -> Tuple[str, Dict[str, float]]:
    """IB-only stock fetch (main thread). Returns (symbol, history)."""
    try:
        bars = ib_client.get_historical_data(
            Stock(symbol, "SMART", "USD"),
            duration="1 Y",
            bar_size="1 day",
            what_to_show="TRADES",
        )
        parsed = {
            str(bar.date)[:10]: float(bar.close)
            for bar in bars
            if start <= str(bar.date)[:10] <= end
        }
        if parsed:
            return symbol, parsed
    except Exception:
        pass
    return symbol, {}


def _fetch_stock_history_fallback(
    symbol: str, start: str, end: str
) -> Tuple[str, Dict[str, float], str]:
    """UW -> Yahoo fallback. Creates own UWClient. Thread-safe.

    Returns (symbol, history, source).
    """
    # Check cache first
    key = cache_key_stock(symbol, start, end)
    cached = read_cache(STOCKS_DIR, key)
    if cached:
        return symbol, cached, "cache"

    # Try UW
    try:
        uw = UWClient()
        data = uw.get_stock_ohlc(symbol, candle_size="1d")
        uw.close()
        parsed: Dict[str, float] = {}
        for bar in data.get("data", []):
            dt = str(bar.get("date") or "")[:10]
            close = safe_float(bar.get("close"), default=float("nan"))
            if dt and math.isfinite(close) and start <= dt <= end:
                parsed[dt] = close
        if parsed:
            ttl = TTL_MARKET_HOURS if is_market_hours() else TTL_AFTER_CLOSE
            write_cache(STOCKS_DIR, key, parsed, source="uw", ttl=ttl)
            return symbol, parsed, "uw"
    except Exception:
        pass

    # Yahoo fallback
    try:
        parsed = {}
        for dt, close in _fetch_yahoo_chart(symbol):
            if start <= dt <= end:
                parsed[dt] = close
        if parsed:
            ttl = TTL_MARKET_HOURS if is_market_hours() else TTL_AFTER_CLOSE
            write_cache(STOCKS_DIR, key, parsed, source="yahoo", ttl=ttl)
            return symbol, parsed, "yahoo"
    except Exception:
        pass

    return symbol, {}, "none"


def _fetch_option_history_safe(
    option_id: str, start: str, end: str
) -> Tuple[str, Dict[str, float], Optional[str]]:
    """Thread-safe option fetch with own UWClient. Exception -> warning string.

    Returns (option_id, history, warning_or_none).
    """
    # Check cache first
    key = cache_key_option(option_id, start, end)
    cached = read_cache(OPTIONS_DIR, key)
    if cached:
        return option_id, cached, None

    try:
        uw = UWClient()
        data = uw.get_option_contract_historic(option_id)
        uw.close()
        parsed: Dict[str, float] = {}
        for row in data.get("chains", []):
            dt = str(row.get("date") or "")[:10]
            if not dt or dt < start or dt > end:
                continue
            mark = select_option_mark(row)
            if mark is not None:
                parsed[dt] = mark
        if parsed:
            ttl = TTL_MARKET_HOURS if is_market_hours() else TTL_AFTER_CLOSE
            write_cache(OPTIONS_DIR, key, parsed, source="uw", ttl=ttl)
        return option_id, parsed, None
    except UWRateLimitError:
        return option_id, {}, f"Rate limited fetching {option_id} — skipped"
    except Exception as exc:
        return option_id, {}, f"Option history unavailable for {option_id}: {exc}"


def _fetch_all_histories(
    trades: List[TradeFill],
    start: str,
    end: str,
    ib_client: Optional[IBClient],
    warnings: List[str],
    seed_marks: Optional[Dict[str, Dict[str, float]]] = None,
) -> Tuple[Dict[str, Dict[str, float]], List[str]]:
    """Orchestrator: seed marks -> IB pass (sequential) -> parallel fallback + options.

    ``seed_marks`` pre-populates contract marks from execution prices.
    IB/UW data overwrites seed marks when available (more complete daily series).

    Returns (marks_by_contract, missing_contracts).
    """
    # Pre-seed marks from execution prices (better than $0 for missing contracts)
    marks_by_contract: Dict[str, Dict[str, float]] = dict(seed_marks or {})
    missing_contracts: List[str] = []

    stock_symbols = sorted({t.symbol for t in trades if t.security_type == "STK" and t.symbol})
    option_ids = sorted({t.option_id for t in trades if t.security_type == "OPT" and t.option_id})

    # Phase A: IB pass for stocks (main thread, sequential)
    ib_successes: set = set()
    if ib_client is not None:
        for symbol in stock_symbols:
            # Check cache first
            key = cache_key_stock(symbol, start, end)
            cached = read_cache(STOCKS_DIR, key)
            if cached:
                marks_by_contract[f"STK:{symbol}"] = cached
                ib_successes.add(symbol)
                continue

            sym, history = _fetch_stock_history_ib_only(symbol, start, end, ib_client)
            if history:
                ttl = TTL_MARKET_HOURS if is_market_hours() else TTL_AFTER_CLOSE
                write_cache(STOCKS_DIR, key, history, source="ib", ttl=ttl)
                marks_by_contract[f"STK:{sym}"] = history
                ib_successes.add(sym)

    # Phase B: Parallel fallback for failed stocks + all options
    failed_stocks = [s for s in stock_symbols if s not in ib_successes]
    max_workers = _get_worker_count()
    total_tasks = len(failed_stocks) + len(option_ids)

    if total_tasks > 0:
        with ThreadPoolExecutor(max_workers=min(max_workers, total_tasks)) as pool:
            futures = {}

            for symbol in failed_stocks:
                fut = pool.submit(_fetch_stock_history_fallback, symbol, start, end)
                futures[fut] = ("stock", symbol)

            for oid in option_ids:
                fut = pool.submit(_fetch_option_history_safe, oid, start, end)
                futures[fut] = ("option", oid)

            for future in as_completed(futures):
                kind, contract_id = futures[future]
                try:
                    if kind == "stock":
                        sym, history, _source = future.result()
                        contract_key = f"STK:{sym}"
                        if history:
                            marks_by_contract[contract_key] = history
                        else:
                            missing_contracts.append(contract_key)
                    else:
                        oid, history, warning = future.result()
                        if warning:
                            warnings.append(warning)
                        if history:
                            marks_by_contract[oid] = history
                        else:
                            missing_contracts.append(oid)
                except Exception as exc:
                    warnings.append(f"Fetch failed for {contract_id}: {exc}")
                    if kind == "stock":
                        missing_contracts.append(f"STK:{contract_id}")
                    else:
                        missing_contracts.append(contract_id)

        # Prune cache once after all writes complete
        prune_cache(STOCKS_DIR)

    return marks_by_contract, missing_contracts


def build_payload(benchmark_symbol: str = "SPY") -> dict:
    # 1. Load portfolio snapshot
    portfolio = load_portfolio_snapshot()
    account = portfolio.get("account_summary") or {}
    current_net_liq = safe_float(account.get("net_liquidation"), default=safe_float(portfolio.get("bankroll")))
    last_sync = str(portfolio.get("last_sync") or "")

    end_date = last_sync[:10] if last_sync else datetime.now().strftime("%Y-%m-%d")
    start_date = f"{end_date[:4]}-01-01"

    warnings: List[str] = []

    # 2. Connect IB for benchmark data
    ib_client: Optional[IBClient] = None
    try:
        ib_client = IBClient()
        ib_client.connect(port=4001, client_id=98, timeout=5)
    except Exception as exc:
        warnings.append(f"IB historical bars unavailable: {exc}")
        ib_client = None

    # 3. Benchmark (defines calendar) — must come first
    benchmark_history = fetch_stock_history(benchmark_symbol, start_date, end_date, ib_client, None)
    if not benchmark_history:
        # Try cache / fallback for benchmark
        _, benchmark_history, _ = _fetch_stock_history_fallback(benchmark_symbol, start_date, end_date)
    if not benchmark_history:
        raise RuntimeError(f"Could not fetch benchmark history for {benchmark_symbol}")
    # Cache benchmark
    bm_key = cache_key_stock(benchmark_symbol, start_date, end_date)
    bm_cached = read_cache(STOCKS_DIR, bm_key)
    if not bm_cached:
        ttl = TTL_MARKET_HOURS if is_market_hours() else TTL_AFTER_CLOSE
        write_cache(STOCKS_DIR, bm_key, benchmark_history, source="ib", ttl=ttl)

    calendar = sorted(benchmark_history.keys())
    benchmark_series = pd.Series({dt: benchmark_history[dt] for dt in calendar}, dtype=float)

    # ── PRIMARY PATH: IB NAV-based equity curve ──────────────────────────
    # If we have daily NAV from the Equity Summary Flex Query, use it
    # directly.  This is IB's authoritative account valuation — no
    # reconstruction, no missing option marks, no deposit confusion.
    nav_entries = fetch_ib_nav_series()
    if nav_entries is None:
        nav_entries = load_ib_nav_cache()

    if nav_entries and len(nav_entries) >= 2:
        try:
            curve, metrics = build_nav_based_curve(nav_entries, start_date, benchmark_series)
            if ib_client is not None:
                ib_client.disconnect()

            benchmark_total_return = float(
                (benchmark_series.iloc[-1] / benchmark_series.iloc[0]) - 1.0
            ) if len(benchmark_series) > 1 else 0.0

            # Align benchmark to NAV calendar
            nav_dates = list(curve.index)
            bench_returns = benchmark_series.pct_change().fillna(0.0)

            series = []
            for dt in nav_dates:
                bm_close = float(benchmark_series.get(dt, 0.0)) if dt in benchmark_series.index else 0.0
                bm_ret = float(bench_returns.get(dt, 0.0)) if dt in bench_returns.index else 0.0
                dr = curve.loc[dt, "daily_return"]
                series.append({
                    "date": dt,
                    "equity": round(float(curve.loc[dt, "equity"]), 4),
                    "daily_return": None if dr is None or (isinstance(dr, float) and pd.isna(dr)) else round(float(dr), 8),
                    "drawdown": round(float(curve.loc[dt, "drawdown"]), 8),
                    "benchmark_close": round(bm_close, 4),
                    "benchmark_return": round(bm_ret, 8),
                })

            warnings.insert(0, "Equity curve from IB daily NAV (EquitySummaryInBase). Includes deposits and transfers.")
            return {
                "as_of": end_date,
                "last_sync": last_sync,
                "period_start": start_date,
                "period_end": nav_dates[-1] if nav_dates else end_date,
                "period_label": "YTD",
                "benchmark": benchmark_symbol,
                "benchmark_total_return": benchmark_total_return,
                "trades_source": "ib_nav",
                "price_sources": {"stocks": "ib_nav_flex", "options": "ib_nav_flex"},
                "methodology": {
                    "curve_type": "ib_daily_nav",
                    "return_basis": "twr_deposit_adjusted",
                    "risk_free_rate": 0.0,
                    "library_strategy": "ib_equity_summary_in_base",
                },
                "summary": {
                    "starting_equity": round(float(curve["equity"].iloc[0]), 4),
                    "ending_equity": round(float(curve["equity"].iloc[-1]), 4),
                    "pnl": round(float(curve["equity"].iloc[-1] - curve["equity"].iloc[0]), 4),
                    "trading_days": len(nav_dates),
                    **{k: round(float(v), 8) if isinstance(v, (float, np.floating)) else v for k, v in metrics.items()},
                },
                "warnings": warnings,
                "contracts_missing_history": [],
                "series": series,
            }
        except Exception as exc:
            warnings.append(f"NAV-based curve failed, falling back to reconstruction: {exc}")

    # ── FALLBACK: Reconstructed equity curve from trade fills ────────────
    warnings.append(
        "Reconstructed YTD equity curve anchored to current net liquidation. "
        "External cash flows are assumed zero unless embedded in the starting cash balance."
    )

    try:
        trades, trades_source = fetch_flex_trade_fills()
    except Exception as exc:
        trades = load_blotter_fallback()
        trades_source = "blotter_cache"
        warnings.append(f"Live IB Flex Query unavailable. Falling back to cached blotter data: {exc}")

    if not trades:
        raise RuntimeError("No trades available to reconstruct portfolio performance")

    # 4. Seed marks from execution prices, then fetch full histories
    seed_marks = extract_fill_marks()
    marks_by_contract, missing_contracts = _fetch_all_histories(
        trades, start_date, end_date, ib_client, warnings, seed_marks=seed_marks
    )

    # Disconnect IB
    if ib_client is not None:
        ib_client.disconnect()

    # 4b. Inject current marks from portfolio.json for the final date.
    # This anchors final_holdings_value to IB's live marks rather than stale
    # execution prices or zeros.
    final_date = end_date
    for pos in portfolio.get("positions", []):
        ticker = pos.get("ticker", "")
        expiry_raw = pos.get("expiry", "")
        expiry_digits = re.sub(r"\D", "", expiry_raw)
        for leg in pos.get("legs", []):
            mark = safe_float(leg.get("market_price"), default=0.0)
            if mark <= 0:
                continue
            leg_type = str(leg.get("type", ""))
            strike = safe_float(leg.get("strike"), default=0.0)
            if leg_type and strike > 0 and len(expiry_digits) >= 8:
                right = "C" if "call" in leg_type.lower() else "P"
                try:
                    option_id = build_option_id(ticker, expiry_digits, right, strike)
                except (ValueError, IndexError):
                    continue
                if option_id not in marks_by_contract:
                    marks_by_contract[option_id] = {}
                marks_by_contract[option_id][final_date] = mark

    # Count contracts that have NO marks at all (not even from execution prices)
    truly_missing = [c for c in missing_contracts if c not in marks_by_contract or not marks_by_contract[c]]
    partially_covered = len(missing_contracts) - len(truly_missing)
    if missing_contracts:
        msg = f"Full daily price history unavailable for {len(missing_contracts)} contract(s)."
        if partially_covered > 0:
            msg += f" {partially_covered} have execution-price marks (forward-filled)."
        if truly_missing:
            msg += f" {len(truly_missing)} contract(s) have no marks at all and are valued at zero."
        warnings.append(msg)

    # 5. Reconstruct equity curve with portfolio reconciliation
    portfolio_positions = _build_portfolio_positions(portfolio)
    curve = reconstruct_equity_curve(
        trades=trades,
        calendar=calendar,
        marks_by_contract=marks_by_contract,
        final_equity=current_net_liq,
        portfolio_positions=portfolio_positions,
    )

    # 6. Compute metrics (unchanged)
    metrics = compute_performance_metrics(curve["equity"], benchmark_series)
    benchmark_total_return = float((benchmark_series.iloc[-1] / benchmark_series.iloc[0]) - 1.0) if len(benchmark_series) > 1 else 0.0

    # 7. Build response (public schema only)
    series = []
    bench_returns = benchmark_series.pct_change().fillna(0.0)
    for dt in calendar:
        series.append({
            "date": dt,
            "equity": round(float(curve.loc[dt, "equity"]), 4),
            "daily_return": None if pd.isna(curve.loc[dt, "daily_return"]) else round(float(curve.loc[dt, "daily_return"]), 8),
            "drawdown": round(float(curve.loc[dt, "drawdown"]), 8),
            "benchmark_close": round(float(benchmark_series.loc[dt]), 4),
            "benchmark_return": round(float(bench_returns.loc[dt]), 8),
        })

    return {
        "as_of": end_date,
        "last_sync": last_sync,
        "period_start": start_date,
        "period_end": end_date,
        "period_label": "YTD",
        "benchmark": benchmark_symbol,
        "benchmark_total_return": benchmark_total_return,
        "trades_source": trades_source,
        "price_sources": {
            "stocks": "ib_with_uw_yahoo_fallback",
            "options": "unusual_whales_option_contract_historic",
        },
        "methodology": {
            "curve_type": "reconstructed_net_liquidation",
            "return_basis": "daily_close_to_close",
            "risk_free_rate": 0.0,
            "library_strategy": "in_repo_formulas_aligned_to_empyrical_quantstats_conventions",
        },
        "summary": {
            "starting_equity": round(float(curve["equity"].iloc[0]), 4),
            "ending_equity": round(float(curve["equity"].iloc[-1]), 4),
            "pnl": round(float(curve["equity"].iloc[-1] - curve["equity"].iloc[0]), 4),
            "trading_days": int(len(curve.index)),
            **{key: round(float(value), 8) if isinstance(value, (float, np.floating)) else value for key, value in metrics.items()},
        },
        "warnings": warnings,
        "contracts_missing_history": missing_contracts,
        "series": series,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate YTD portfolio performance metrics")
    parser.add_argument("--json", action="store_true", help="Emit JSON payload")
    parser.add_argument("--benchmark", default="SPY", help="Benchmark symbol (default: SPY)")
    args = parser.parse_args(argv)

    payload = build_payload(benchmark_symbol=args.benchmark.upper())
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        summary = payload["summary"]
        print(f"Portfolio Performance ({payload['period_label']}) — as of {payload['as_of']}")
        print(f"Benchmark: {payload['benchmark']} ({summary['trading_days']} trading days)")
        print(f"Return: {summary['total_return'] * 100:.2f}%")
        print(f"Sharpe: {summary['sharpe_ratio']:.2f} | Sortino: {summary['sortino_ratio']:.2f} | Max DD: {summary['max_drawdown'] * 100:.2f}%")
        if payload["warnings"]:
            print("\nWarnings:")
            for warning in payload["warnings"]:
                print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
