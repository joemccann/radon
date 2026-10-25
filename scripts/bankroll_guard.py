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
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from kelly import KELLY_MAX_PCT
from order_limits import (
    _finite,
    _ib_order_type,
    combo_debit_floor,
    combo_max_loss,
    effective_order_price,
    effective_order_price_bounds,
)

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


_RESTING_STATUSES = {
    "PENDINGSUBMIT",
    "PRESUBMITTED",
    "SUBMITTED",
    "APIPENDING",
    "PENDINGCANCEL",
}


def _load_working_orders() -> Optional[list[dict]]:
    """Today's working orders, or None when the book cannot be read."""
    try:
        from db.readers import read_open_orders

        orders = read_open_orders()
    except Exception:  # noqa: BLE001 - unreadable book keeps the refusal
        return None
    return orders if isinstance(orders, list) else None


def _resting_changes(orders: Any) -> Optional[dict[tuple, tuple[float, float]]]:
    """key -> ``(gross selling, gross buying)`` still resting per contract.

    DS-2026-10-23-03: the close exemption read held quantities alone, so each
    of several closes of one holding was individually only reducing. The
    unfilled remainder of a working order has already laid claim to that size.
    None when the book is unusable, which keeps the cap in force.

    The two directions are kept GROSS and separate. Summing them into one
    signed change let a working OPEN cancel a working close's reservation: a
    resting sell of ten and a resting buy of ten netted to zero against ten
    held contracts, the buy need never fill, and a second sell of ten was
    exempted again.
    """
    if not isinstance(orders, list):
        return None
    try:
        from order_limits import _working_order_shape
    except ImportError:  # pragma: no cover - flat vs package import
        from scripts.order_limits import _working_order_shape  # type: ignore[no-redef]

    resting: dict[tuple, tuple[float, float]] = {}
    for order in orders:
        if not isinstance(order, dict):
            return None
        status = str(order.get("status") or "").upper().replace(" ", "")
        if status and status not in _RESTING_STATUSES:
            continue
        remaining = _finite(order.get("remaining"))
        if remaining is None:
            total = _finite(order.get("totalQuantity")) or _finite(order.get("quantity"))
            filled = _finite(order.get("filled")) or 0.0
            remaining = None if total is None else total - filled
        if remaining is None:
            return None
        if remaining <= 0:
            continue

        contract = order.get("contract")
        contract = contract if isinstance(contract, dict) else {}
        order_type, legs = _working_order_shape(order)
        params = {
            "type": order_type,
            "symbol": contract.get("symbol") or order.get("underlying") or "",
            "action": order.get("action"),
            "quantity": remaining,
            "strike": contract.get("strike"),
            "right": contract.get("right"),
            "expiry": contract.get("expiry"),
        }
        if legs is not None:
            params["legs"] = legs
        order_changes = _order_legs(params)
        if order_changes is None:
            # A working order whose effect cannot be measured must not be
            # treated as reserving nothing.
            return None
        for key, change in order_changes:
            if _finite(change) is None:
                return None
            selling, buying = resting.get(key, (0.0, 0.0))
            if change < 0:
                selling += change
            else:
                buying += change
            resting[key] = (selling, buying)
    return resting


# DS-2026-10-24-02: the placement funnel's broker-confirmed book. While set,
# is_close_out measures holdings and resting orders from the broker instead of
# the store, whose snapshot can still show a holding a filled close removed.
_BROKER_BOOK: Any = ...

# DS-2026-10-25-01: set when a store-measured close is proven, so the funnel
# re-admits at the broker an order its admission exempted even after the
# store stops showing the close.
_STORE_CLOSE_PROVEN = False


def take_store_close_proven() -> bool:
    """Whether a store-measured close was proven since the last call."""
    global _STORE_CLOSE_PROVEN
    proven, _STORE_CLOSE_PROVEN = _STORE_CLOSE_PROVEN, False
    return proven


@contextmanager
def broker_book(book: Optional[tuple[dict, dict]]):
    """Measure close-outs against ``book`` (None: no close is provable)."""
    global _BROKER_BOOK
    previous, _BROKER_BOOK = _BROKER_BOOK, book
    try:
        yield
    finally:
        _BROKER_BOOK = previous


def _contract_key(contract: Any) -> Optional[tuple]:
    symbol = str(getattr(contract, "symbol", "") or "").upper()
    sec_type = str(getattr(contract, "secType", "") or "")
    if sec_type == "STK":
        return (symbol, "STK", None, "")
    if sec_type == "OPT":
        return (
            symbol,
            _norm_right(getattr(contract, "right", "")),
            _finite(getattr(contract, "strike", None)),
            _norm_expiry(getattr(contract, "lastTradeDateOrContractMonth", "")),
        )
    return None


def _con_ids(contract: Any) -> set:
    if str(getattr(contract, "secType", "") or "") == "BAG":
        return {getattr(leg, "conId", None) for leg in getattr(contract, "comboLegs", None) or []}
    return {getattr(contract, "conId", None)}


def _same_order(trade: Any, other: Any) -> bool:
    perm_id = getattr(other.order, "permId", None)
    if perm_id:
        return getattr(trade.order, "permId", None) == perm_id
    return getattr(trade.order, "orderId", None) == getattr(other.order, "orderId", None)


def read_broker_book(
    client: Any, contract: Any = None, exclude: Any = None
) -> Optional[tuple[dict, dict]]:
    """(held, resting) from the connected broker; None when unreadable.

    Same shapes as ``_held_quantities`` / ``_resting_changes``. A combo leg's
    conId resolves through the held positions: a leg on a contract nobody
    holds cannot reserve held size.

    ``contract``: the order's broker contract. Only a position in that
    contract (or a combo's leg contracts) is held, since the coordinate key
    drops the trading class (DS-2026-10-25-03). ``exclude``: a working trade
    whose own remainder reserves nothing, because a modify replaces it.
    """
    try:
        wanted = None if contract is None else _con_ids(contract)
        held: dict[tuple, float] = {}
        by_con_id: dict[Any, tuple] = {}
        for pos in client.get_positions():
            key = _contract_key(pos.contract)
            size = _finite(pos.position)
            if key is None or size is None:
                continue
            con_id = getattr(pos.contract, "conId", None)
            by_con_id[con_id] = key
            if wanted is not None and con_id not in wanted:
                continue
            held[key] = held.get(key, 0.0) + size
        resting: dict[tuple, tuple[float, float]] = {}
        for trade in client.get_open_orders():
            if exclude is not None and _same_order(trade, exclude):
                continue
            status = str(trade.orderStatus.status or "").upper().replace(" ", "")
            if status not in _RESTING_STATUSES:
                continue
            remaining = _finite(trade.orderStatus.remaining)
            if remaining is None:
                return None
            if remaining <= 0:
                continue
            sell = str(trade.order.action or "").upper().startswith("SELL")
            if str(getattr(trade.contract, "secType", "")) == "BAG":
                changes = []
                for leg in trade.contract.comboLegs or []:
                    key = by_con_id.get(leg.conId)
                    if key is None:
                        continue
                    leg_sell = str(leg.action or "").upper().startswith("SELL") != sell
                    ratio = abs(_finite(leg.ratio) or 1.0)
                    changes.append((key, (-1.0 if leg_sell else 1.0) * remaining * ratio))
            else:
                key = _contract_key(trade.contract)
                changes = [] if key is None else [(key, (-1.0 if sell else 1.0) * remaining)]
            for key, change in changes:
                selling, buying = resting.get(key, (0.0, 0.0))
                if change < 0:
                    selling += change
                else:
                    buying += change
                resting[key] = (selling, buying)
        return held, resting
    except Exception:  # noqa: BLE001 - an unreadable broker book proves no close
        return None


def _available_position(position: float, resting: tuple[float, float]) -> float:
    """``position`` less the part a resting order has already claimed.

    Only the opposing side counts, at its GROSS size: a resting open may never
    fill, so it neither creates closeable size nor releases what a resting
    close already reserved.
    """
    selling, buying = resting
    if position > 0:
        return max(position + selling, 0.0)
    if position < 0:
        return min(position + buying, 0.0)
    return position


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


def fresh_reducing_close(
    params: dict,
    snapshot: Optional[dict],
    now: datetime,
    working_orders: Any = ...,
) -> bool:
    """True when the order only reduces holdings on a snapshot still in the 15-minute window.

    A SELL-envelope combo is priced as new short assignment by the fat-finger
    loss cap. That price is right for an opening order. It is the wrong price
    for a close of longs the book still holds: ten SPX puts sold to close
    clear the $10M cap on strike proxy alone. The snapshot has to be fresh.
    A stale book must not waive the cap, because the position may already be
    gone and the same bytes would open the short. A client flag is not proof.
    """
    if not is_close_out(params, snapshot, working_orders=working_orders) or not isinstance(
        snapshot, dict
    ):
        return False
    synced = _parse_ts(snapshot.get("last_sync"))
    age = (now - synced).total_seconds() if synced else None
    if age is None or age > MAX_SNAPSHOT_AGE_SECS or age < -_CLOCK_SKEW_SECS:
        return False
    return True


def is_close_out(
    params: dict, snapshot: Optional[dict], working_orders: Any = ...
) -> bool:
    """True when the combined delta per contract reduces holdings without a flip.

    The holding is measured net of what working orders have already claimed
    (DS-2026-10-23-03). ``working_orders`` defaults to a read of today's book;
    pass a list to supply it, and a book that cannot be read is not a waiver.
    Inside ``broker_book`` both come from the broker instead (DS-2026-10-24-02).
    """
    if not isinstance(snapshot, dict):
        return False
    legs = _order_legs(params)
    if not legs:
        return False
    if _BROKER_BOOK is not ...:
        if _BROKER_BOOK is None:
            return False
        held, resting = _BROKER_BOOK
    else:
        if working_orders is ...:
            working_orders = _load_working_orders()
        resting = _resting_changes(working_orders)
        if resting is None:
            return False
        held = _held_quantities(snapshot)
    changes: dict[tuple, float] = {}
    for key, change in legs:
        if _finite(change) is None:
            return False
        changes[key] = changes.get(key, 0.0) + change
    for key, change in changes.items():
        if _finite(change) is None:
            return False
        position = _available_position(held.get(key, 0.0), resting.get(key, (0.0, 0.0)))
        if position == 0 or change == 0 or (position > 0) == (change > 0):
            return False
        if abs(change) > abs(position) + 1e-9:
            return False
    if _BROKER_BOOK is ...:
        global _STORE_CLOSE_PROVEN
        _STORE_CLOSE_PROVEN = True
    return True


def _combo_expiry_loss(legs: Any, quantity: float, price: float, sell: bool) -> Optional[float]:
    """Exact worst case at expiry for single-expiry option combos.

    The payoff is piecewise linear, so its minimum sits at S=0 or a strike
    unless calls are net short (unbounded). A debit structure therefore risks
    its premium, not its width. None (fall back to the margin proxy) for
    stock legs, calendars, or a net short call tail.
    """
    if not isinstance(legs, list) or not legs:
        return None
    parsed = []
    for leg in legs:
        if not isinstance(leg, dict):
            return None
        right = _norm_right(leg.get("right"))
        strike = _finite(leg.get("strike"))
        ratio = _finite(leg.get("ratio", 1) or 1)
        if right not in ("C", "P") or strike is None or strike <= 0 or ratio is None or ratio <= 0:
            return None
        side = -1.0 if str(leg.get("action") or "").upper().startswith("SELL") else 1.0
        parsed.append((right, strike, side * ratio, _norm_expiry(leg.get("expiry"))))
    if len({expiry for *_, expiry in parsed}) != 1:
        return None
    if sum(signed for right, _, signed, _ in parsed if right == "C") < 0:
        return None

    def payoff(spot: float) -> float:
        return sum(
            signed * (max(spot - strike, 0.0) if right == "C" else max(strike - spot, 0.0))
            for right, strike, signed, _ in parsed
        )

    worst = min(payoff(spot) for spot in [0.0] + [strike for _, strike, _, _ in parsed])
    is_debit = (price > 0) != sell
    cost = abs(price) if is_debit else -abs(price)
    return abs(quantity) * max(cost - worst, 0.0) * 100.0


def order_max_loss(params: dict) -> Optional[float]:
    """Defined worst-case loss in dollars; None when undefined or unpriceable."""
    order_type = str(params.get("type") or "stock").lower()
    if order_type == "combo":
        if str(params.get("action") or "").upper().startswith("SELL"):
            legs = params.get("legs")
            if not isinstance(legs, list) or any(not isinstance(leg, dict) for leg in legs):
                return None
            # A SELL BAG reverses each leg's execution direction. Keep the
            # envelope and price intact so the premium sign is still correct.
            # `combo_max_loss` performs this reversal ITSELF from the envelope,
            # so it keeps the ORIGINAL params: handing it the already-reversed
            # legs reversed them twice, and a SELL BAG of BUY call legs (short
            # calls at IB) reached the margin proxy as all-long calls and
            # priced at zero, which both Gate-3 guards then consumed.
            as_executed = {**params, "legs": [
                {**leg, "action": "BUY" if str(leg.get("action") or "").upper().startswith("SELL") else "SELL"}
                for leg in legs
            ]}
        else:
            as_executed = params
        quantity = _finite(params.get("quantity"))
        # Stop-market transmits stopPrice only. Price the envelope as paying
        # the worse supplied price so a discarded credit limit cannot shrink
        # assignment. Limit orders keep the signed net price.
        stop_market = _ib_order_type(params) == "STP"
        if stop_market:
            price = effective_order_price(params)
            if quantity is None or quantity == 0 or price <= 0:
                return None
        else:
            price = _finite(params.get("limitPrice") or params.get("stopPrice"))
            if quantity is None or price is None or quantity == 0 or price == 0:
                return None
        sell = str(params.get("action") or "").upper().startswith("SELL")
        expiry_sell = False if stop_market else sell
        expiry_loss = _combo_expiry_loss(
            as_executed.get("legs"), quantity, price, expiry_sell
        )
        if expiry_loss is not None:
            return expiry_loss
        loss = _finite(combo_max_loss(params))
        if loss is None:
            return None
        # The margin proxy can be zero for all-long legs; premium paid is
        # still exposed even when there is no short-leg assignment risk.
        # A stop-market transmits stopPrice only, so the shared floor's
        # signed limit is the discarded field (REL-328). Count the worse
        # supplied price as paid. A limit order uses the shared floor so
        # both Gate-3 guards stay on one implementation.
        if stop_market:
            return max(loss, abs(quantity * price) * 100.0)
        return combo_debit_floor(params, loss)
    sell = str(params.get("action") or "").upper().startswith("SELL")
    quantity = abs(_finite(params.get("quantity")) or 0.0)
    # The price IB actually sends for this order type: a STP order carries no
    # limit, so a decoy limitPrice must not price the cap (see
    # order_limits.effective_order_price).
    price = effective_order_price(params)
    if quantity <= 0 or price <= 0:
        return None
    if order_type == "option":
        if not sell:
            return quantity * price * 100.0
        if _norm_right(params.get("right")) == "P":
            strike = _finite(params.get("strike")) or 0.0
            # Assignment loss falls as the premium collected rises, so the
            # conservative end of a stop order's price range is the low one.
            credit = effective_order_price_bounds(params)[0]
            return quantity * max(strike - credit, 0.0) * 100.0 if strike > 0 else None
        return None
    if order_type == "stock" and not sell:
        return quantity * price
    return None


ENFORCE_ALL_PATHS_PREF = "RADON_BANKROLL_CAP_ENFORCE_ALL_PATHS"


def enforced_on_all_paths() -> bool:
    """The operator preference, default Off (bypass).

    These processes are not spawned by radon-api (no env overlay) and the
    exit service is long-lived, so re-read the store per placement; a dead
    store keeps the last known value, else env/default.
    """
    try:
        import app_preferences
    except ImportError:
        from scripts import app_preferences

    if app_preferences._db_enabled():
        app_preferences.refresh_snapshot()
    return app_preferences.get_bool(ENFORCE_ALL_PATHS_PREF)


def check_if_enforced_on_all_paths(params: dict) -> Optional[dict[str, Any]]:
    """Gate 3 for placers outside ib_place_order (ib_execute, exit service)."""
    if not enforced_on_all_paths():
        return None
    return check_bankroll_admission(params)


def check_bankroll_admission(
    params: dict,
    *,
    snapshot: Any = ...,
    now: Optional[datetime] = None,
    working_orders: Any = ...,
) -> Optional[dict[str, Any]]:
    """Return {"code", "message"} on refusal, None when admitted."""
    if snapshot is ...:
        snapshot = _load_latest_snapshot()
    now = now or _utcnow()

    if is_close_out(params, snapshot, working_orders=working_orders):
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
