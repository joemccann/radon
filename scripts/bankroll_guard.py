"""Gate 3 server-side: max loss per order <= 2.5% of bankroll (NF-1).

Bankroll is IB net liquidation from the latest portfolio sync's
``account_summary`` (Turso ``portfolio_snapshots``). It must be at most
15 minutes old by the snapshot's ``last_sync``; stale, missing, non-finite
or non-positive NLV refuses the order.

A close-out is never blocked. It is identified from the snapshot's own held
positions (every leg reduces a held leg by no more than its size), not from a
client-supplied flag.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from kelly import KELLY_MAX_PCT
from order_limits import _finite, combo_max_loss

MAX_SNAPSHOT_AGE_SECS = 15 * 60
_CLOCK_SKEW_SECS = 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_latest_snapshot() -> Optional[dict]:
    try:
        from api import db_http

        rows = db_http.hrana_execute(
            "SELECT payload FROM portfolio_snapshots ORDER BY taken_at DESC LIMIT 1", ()
        )
    except Exception:  # noqa: BLE001 - unreadable store refuses, never admits
        return None
    if not rows or not rows[0]:
        return None
    payload = rows[0][0]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _norm_expiry(raw: Any) -> str:
    return str(raw or "").replace("-", "")


def _norm_right(raw: Any) -> str:
    value = str(raw or "").upper()
    return {"CALL": "C", "PUT": "P", "STOCK": "STK", "FUTURE": "FUT"}.get(value, value)


def _held_quantities(snapshot: dict) -> dict[tuple, float]:
    """(symbol, right, strike, expiry) -> signed held contracts/shares."""
    held: dict[tuple, float] = {}
    for pos in snapshot.get("positions") or []:
        if not isinstance(pos, dict):
            continue
        symbol = str(pos.get("ticker") or "").upper()
        for leg in pos.get("legs") or []:
            if not isinstance(leg, dict):
                continue
            right = _norm_right(leg.get("type"))
            is_option = right in ("C", "P")
            key = (
                symbol,
                right,
                _finite(leg.get("strike")) if is_option else None,
                _norm_expiry(leg.get("expiry") or pos.get("expiry")) if is_option else "",
            )
            size = abs(_finite(leg.get("contracts")) or 0.0)
            sign = -1.0 if str(leg.get("direction") or "").upper() == "SHORT" else 1.0
            held[key] = held.get(key, 0.0) + sign * size
    return held


def _order_legs(params: dict) -> Optional[list[tuple[tuple, float]]]:
    """[(key, signed quantity change)] for the order; None when unmatchable."""
    symbol = str(params.get("symbol") or "").upper()
    try:
        quantity = abs(float(params.get("quantity") or 0))
    except (TypeError, ValueError):
        return None
    envelope_sell = str(params.get("action") or "").upper().startswith("SELL")
    order_type = str(params.get("type") or "stock").lower()

    if order_type == "combo":
        out = []
        for leg in params.get("legs") or []:
            if not isinstance(leg, dict):
                return None
            leg_sell = str(leg.get("action") or "").upper().startswith("SELL")
            sell = leg_sell != envelope_sell
            try:
                ratio = abs(float(leg.get("ratio", 1) or 1))
            except (TypeError, ValueError):
                return None
            key = (symbol, _norm_right(leg.get("right")), _finite(leg.get("strike")), _norm_expiry(leg.get("expiry")))
            out.append((key, (-1.0 if sell else 1.0) * quantity * ratio))
        return out or None
    if order_type == "option":
        key = (symbol, _norm_right(params.get("right")), _finite(params.get("strike")), _norm_expiry(params.get("expiry")))
    elif order_type == "stock":
        key = (symbol, "STK", None, "")
    else:
        return None
    return [(key, (-1.0 if envelope_sell else 1.0) * quantity)]


def is_close_out(params: dict, snapshot: Optional[dict]) -> bool:
    """True when every leg only reduces a held position, never flips it."""
    if not isinstance(snapshot, dict):
        return False
    legs = _order_legs(params)
    if not legs:
        return False
    held = _held_quantities(snapshot)
    for key, change in legs:
        position = held.get(key, 0.0)
        if position == 0 or change == 0 or (position > 0) == (change > 0):
            return False
        if abs(change) > abs(position) + 1e-9:
            return False
    return True


def order_max_loss(params: dict) -> Optional[float]:
    """Defined worst-case loss in dollars; None when undefined or unpriceable."""
    order_type = str(params.get("type") or "stock").lower()
    if order_type == "combo":
        return combo_max_loss(params)
    sell = str(params.get("action") or "").upper().startswith("SELL")
    quantity = abs(_finite(params.get("quantity")) or 0.0)
    price = abs(_finite(params.get("limitPrice")) or _finite(params.get("stopPrice")) or 0.0)
    if quantity <= 0 or price <= 0:
        return None
    if order_type == "option":
        if not sell:
            return quantity * price * 100.0
        if _norm_right(params.get("right")) == "P":
            strike = _finite(params.get("strike")) or 0.0
            return quantity * max(strike - price, 0.0) * 100.0 if strike > 0 else None
        return None
    if order_type == "stock" and not sell:
        return quantity * price
    return None


def check_bankroll_admission(
    params: dict,
    *,
    snapshot: Any = ...,
    now: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """Return {"code", "message"} on refusal, None when admitted."""
    if snapshot is ...:
        snapshot = _load_latest_snapshot()
    now = now or _utcnow()

    if is_close_out(params, snapshot):
        return None

    if not isinstance(snapshot, dict):
        return {
            "code": "BANKROLL_UNAVAILABLE",
            "message": (
                "Order refused: no portfolio sync is available, so net liquidation "
                "(bankroll) is unknown and the 2.5% position cap cannot be checked. "
                "Run a portfolio sync and retry. Closing a held position is still allowed."
            ),
        }

    nlv = _finite((snapshot.get("account_summary") or {}).get("net_liquidation"))
    if nlv is None or nlv <= 0:
        return {
            "code": "BANKROLL_UNAVAILABLE",
            "message": (
                "Order refused: the last portfolio sync has no valid net liquidation, "
                "so the 2.5% position cap cannot be checked. Run a portfolio sync and "
                "retry. Closing a held position is still allowed."
            ),
        }

    synced = _parse_ts(snapshot.get("last_sync"))
    age = (now - synced).total_seconds() if synced else None
    if age is None or age > MAX_SNAPSHOT_AGE_SECS or age < -_CLOCK_SKEW_SECS:
        age_text = f"{age / 60:.0f} min old" if age is not None else "undated"
        return {
            "code": "BANKROLL_STALE",
            "message": (
                f"Order refused: net liquidation is {age_text} (limit 15 min), so the "
                "2.5% position cap cannot be checked. Run a portfolio sync and retry. "
                "Closing a held position is still allowed."
            ),
        }

    loss = order_max_loss(params)
    if loss is None:
        return {
            "code": "BANKROLL_UNDEFINED_RISK",
            "message": (
                "Order refused: max loss is undefined or cannot be priced, so it "
                "cannot be held under the 2.5% position cap. Use a defined-risk structure."
            ),
        }

    cap = nlv * KELLY_MAX_PCT
    if loss > cap + 1e-6:
        return {
            "code": "BANKROLL_CAP_EXCEEDED",
            "message": (
                f"Order refused: max loss ${loss:,.0f} is {loss / nlv * 100:.2f}% of "
                f"net liquidation ${nlv:,.0f}; the cap is 2.5% (${cap:,.0f})."
            ),
        }
    return None
