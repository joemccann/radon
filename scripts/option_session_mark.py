"""After-hours option mark: last trade, else last bid/offer mid. Never previous close.

IB's option CLOSE tick stays on the previous session after 16:00 ET. On
2026-09-21 the META 16 Oct 2026 665 put closed the tape at 8.10 (15:59 ET
print, volume 50) with a 15:59 midpoint of 8.15, while tick 9 CLOSE was
26.70. Sync and the UI had been promoting that close to Last Price.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable, Optional

MARK_FIELDS = ("last", "bid", "ask", "mid")
MARK_CHUNK = 200


def _positive_price(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0 or price >= 1e307:
        return None
    return price


def _nonneg_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def option_mark_key(symbol, expiry, strike, right) -> Optional[str]:
    symbol_value = str(symbol or "").strip().upper()
    if not symbol_value:
        return None
    digits = "".join(ch for ch in str(expiry or "") if ch.isdigit())
    if len(digits) == 6:
        digits = f"20{digits}"
    if len(digits) != 8:
        return None
    right_value = str(right or "").strip().upper()[:1]
    if right_value not in {"C", "P"}:
        return None
    try:
        strike_value = float(strike)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(strike_value) or strike_value <= 0:
        return None
    if strike_value.is_integer():
        strike_token = str(int(strike_value))
    else:
        strike_token = f"{strike_value:.4f}".rstrip("0").rstrip(".")
    return f"{symbol_value}_{digits}_{strike_token}_{right_value}"


def session_is_fresh(entry: Optional[dict], today: str) -> bool:
    return isinstance(entry, dict) and entry.get("checked_on") == today


def _bar_fields(bar: Any) -> tuple[Any, Any]:
    if isinstance(bar, dict):
        return bar.get("close"), bar.get("volume")
    return getattr(bar, "close", None), getattr(bar, "volume", None)


def last_traded_bar_price(bars) -> Optional[float]:
    """Last 1-minute TRADES bar with volume. Empty bars forward-fill a stale close."""
    if not bars:
        return None
    for bar in reversed(list(bars)):
        close, volume = _bar_fields(bar)
        price = _positive_price(close)
        vol = _nonneg_number(volume)
        if price is not None and vol is not None and vol > 0:
            return price
    return None


def last_midpoint_bar_price(bars) -> Optional[float]:
    if not bars:
        return None
    for bar in reversed(list(bars)):
        close, _ = _bar_fields(bar)
        price = _positive_price(close)
        if price is not None:
            return price
    return None


def _from_session(mark: Optional[dict]) -> tuple[Optional[float], bool, bool]:
    if not mark:
        return None, False, False
    last = _positive_price(mark.get("last"))
    if last is not None:
        return last, False, True
    bid = _positive_price(mark.get("bid"))
    ask = _positive_price(mark.get("ask"))
    if bid is not None and ask is not None:
        return round((bid + ask) / 2, 4), True, True
    mid = _positive_price(mark.get("mid"))
    if mid is not None:
        return mid, True, True
    return None, False, False


def resolve_polled_mark(
    market_price: Optional[float],
    bid: Optional[float],
    ask: Optional[float],
    close: Optional[float] = None,
    *,
    sec_type: Optional[str] = None,
    session_mark: Optional[dict] = None,
    session_is_fresh: bool = True,
    trade_bars=None,
    midpoint_bars=None,
) -> tuple[Optional[float], bool]:
    """Live last / live book, then session last or last bid/offer, then history.

    Option previous-session CLOSE is never a mark.
    """
    market_price = _positive_price(market_price)
    bid = _positive_price(bid)
    ask = _positive_price(ask)
    close = _positive_price(close)
    if market_price is not None:
        return market_price, False
    if bid is not None and ask is not None:
        return round((bid + ask) / 2, 4), True

    if session_is_fresh:
        price, calculated, hit = _from_session(session_mark)
        if hit:
            return price, calculated

    trade = last_traded_bar_price(trade_bars)
    if trade is not None:
        return trade, False
    mid = last_midpoint_bar_price(midpoint_bars)
    if mid is not None:
        return mid, True

    if not session_is_fresh:
        price, calculated, hit = _from_session(session_mark)
        if hit:
            return price, calculated

    if str(sec_type or "").upper() == "OPT":
        return None, False
    if close is not None:
        return close, True
    return None, False


def merge_session_mark(
    existing: Optional[dict],
    *,
    today: str,
    trade: Optional[float] = None,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    mid: Optional[float] = None,
    stamp_checked: bool = False,
) -> Optional[dict]:
    entry = dict(existing or {})
    changed = False
    trade = _positive_price(trade)
    bid = _positive_price(bid)
    ask = _positive_price(ask)
    mid = _positive_price(mid)
    if trade is not None and entry.get("last") != trade:
        entry["last"] = trade
        changed = True
    if bid is not None and ask is not None and (
        entry.get("bid") != bid or entry.get("ask") != ask
    ):
        entry["bid"] = bid
        entry["ask"] = ask
        changed = True
    if mid is not None and entry.get("last") is None and entry.get("mid") != mid:
        entry["mid"] = mid
        changed = True
    if today and (
        stamp_checked or trade is not None or (bid is not None and ask is not None) or mid is not None
    ) and entry.get("checked_on") != today:
        if entry.get("last") is not None or (
            entry.get("bid") is not None and entry.get("ask") is not None
        ) or entry.get("mid") is not None or stamp_checked:
            entry["checked_on"] = today
            changed = True
    if not changed:
        return None
    return entry


def poll_contract_mark(
    *,
    sec_type: Optional[str],
    market_price: Optional[float],
    bid: Optional[float],
    ask: Optional[float],
    close: Optional[float],
    trade: Optional[float],
    session_mark: Optional[dict],
    today: str,
    fetch_history: Optional[Callable[[], tuple[Any, Any]]] = None,
    volume: Optional[float] = None,
    broker_mark: Optional[float] = None,
) -> tuple[Optional[float], bool, Optional[dict]]:
    live_price = _positive_price(market_price)
    live_bid = _positive_price(bid)
    live_ask = _positive_price(ask)
    live_book = live_bid is not None and live_ask is not None
    live = live_price is not None or live_book
    fresh = session_is_fresh(session_mark, today)
    is_opt = str(sec_type or "").upper() == "OPT"
    # IB reports no trade today and no two-sided book: any last in hand is an
    # older print, so IB's own position mark is the only current value.
    broker = _positive_price(broker_mark)
    if is_opt and broker is not None and not live_book and _nonneg_number(volume) == 0:
        return broker, True, None
    need_history = is_opt and not live and not fresh
    trade_bars = midpoint_bars = None
    if need_history and fetch_history is not None:
        try:
            result = fetch_history()
        except Exception:
            result = None
        if result:
            trade_bars, midpoint_bars = result[0], result[1]

    price, calculated = resolve_polled_mark(
        live_price,
        live_bid,
        live_ask,
        close,
        sec_type=sec_type,
        session_mark=session_mark,
        session_is_fresh=fresh,
        trade_bars=trade_bars,
        midpoint_bars=midpoint_bars,
    )

    if not is_opt:
        return price, calculated, None

    tick_last = _positive_price(trade)
    # Persist only observed evidence. The resolved price may be yesterday's
    # fallback after failed/empty history and must not suppress the next poll.
    store_trade = tick_last or live_price or last_traded_bar_price(trade_bars)
    store_mid = last_midpoint_bar_price(midpoint_bars) if store_trade is None else None
    if store_trade is None and not live_book and store_mid is None:
        return price, calculated, None
    updated = merge_session_mark(
        # A new book/midpoint must not re-date an older last trade that would
        # take precedence on subsequent cache-only reads.
        session_mark if fresh else None,
        today=today,
        trade=store_trade,
        bid=live_bid,
        ask=live_ask,
        mid=store_mid,
    )
    return price, calculated, updated


def _read_rows(sql: str, args: tuple) -> list:
    from api import db_http

    return db_http.hrana_execute(sql, args)


def _write(sql: str, args: tuple) -> None:
    from db.hrana_http import hrana_execute

    hrana_execute(sql, args)


def load_session_marks(keys: Iterable[str]) -> dict:
    """Stored marks for these contracts. A failed read is an empty cache: the
    sync falls through to live quotes and history instead of failing."""
    wanted = sorted({key for key in keys if key})
    out: dict[str, dict] = {}
    try:
        for start in range(0, len(wanted), MARK_CHUNK):
            chunk = wanted[start:start + MARK_CHUNK]
            slots = ",".join("?" * len(chunk))
            rows = _read_rows(
                f"SELECT mark_key, last, bid, ask, mid, checked_on FROM option_session_marks "
                f"WHERE mark_key IN ({slots})",
                tuple(chunk),
            )
            for key, *prices, checked in rows:
                entry: dict[str, Any] = {
                    field: price
                    for field, price in zip(MARK_FIELDS, map(_positive_price, prices))
                    if price is not None
                }
                if checked:
                    entry["checked_on"] = checked
                if entry:
                    out[key] = entry
    except Exception as exc:
        print(f"  Warning: option session marks unreadable: {exc}")
        return {}
    return out


def save_session_marks(marks: dict) -> None:
    """Upsert marks. Prices are validated so a close-shaped or zero value never
    lands as a last. A failed write loses one cache update, never the sync."""
    rows = []
    for key, val in marks.items():
        if not isinstance(key, str) or not isinstance(val, dict):
            continue
        prices = [_positive_price(val.get(field)) for field in MARK_FIELDS]
        checked = val.get("checked_on") if isinstance(val.get("checked_on"), str) else None
        if any(price is not None for price in prices) or checked:
            rows.append((key, *prices, checked))
    try:
        for start in range(0, len(rows), MARK_CHUNK):
            chunk = rows[start:start + MARK_CHUNK]
            slots = ",".join(["(?,?,?,?,?,?)"] * len(chunk))
            _write(
                f"INSERT OR REPLACE INTO option_session_marks "
                f"(mark_key, last, bid, ask, mid, checked_on) VALUES {slots}",
                tuple(value for row in chunk for value in row),
            )
    except Exception as exc:
        print(f"  Warning: option session marks not saved: {exc}")
