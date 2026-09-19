"""Optional order-path Kelly guard. Off unless RADON_KELLY_ENFORCE_ORDERS=1.

When armed, violations WARN by default (RADON_KELLY_ENFORCE_MODE=warn).
Set RADON_KELLY_ENFORCE_MODE=block to restore hard refuse. Evaluate M6
fail-closed is unchanged. order_limits.py stays fat-finger only. This
module does not re-enable Gate 4.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Optional

from kelly import KELLY_MAX_PCT, kelly_config
from order_limits import combo_max_loss

_LOG = logging.getLogger(__name__)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PORTFOLIO = _REPO_ROOT / "data" / "portfolio.json"


def _is_closing(params: dict) -> bool:
    if params.get("isClosing") or params.get("is_closing"):
        return True
    if params.get("closeOut") or params.get("reducing"):
        return True
    return False


def _portfolio_bankroll() -> Optional[float]:
    try:
        payload = json.loads(_PORTFOLIO.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    acct = payload.get("account_summary") or {}
    for key in ("net_liquidation", "NetLiquidation"):
        raw = acct.get(key)
        if raw is None:
            raw = payload.get(key)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            return value
    try:
        bankroll = float(payload.get("bankroll"))
    except (TypeError, ValueError):
        return None
    return bankroll if math.isfinite(bankroll) and bankroll > 0 else None


def resolve_kelly_bankroll(bankroll: Optional[float] = None) -> Optional[float]:
    if bankroll is not None:
        try:
            value = float(bankroll)
        except (TypeError, ValueError):
            value = float("nan")
        if math.isfinite(value) and value > 0:
            return value
    return _portfolio_bankroll()


def _worst_case_loss(params: dict) -> Optional[float]:
    order_type = str(params.get("type") or "stock").lower()
    if order_type == "combo":
        return combo_max_loss(params)
    if order_type == "option":
        action = str(params.get("action") or "").upper()
        if action.startswith("SELL"):
            return None
        try:
            qty = abs(float(params.get("quantity") or 0))
            price = abs(float(params.get("limitPrice") or 0))
        except (TypeError, ValueError):
            return None
        if qty <= 0 or price <= 0:
            return None
        return qty * price * 100.0
    return None


def _violation(code: str, message: str, **fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    for key, value in fields.items():
        if value is not None:
            payload[key] = value
    extras = []
    if "loss" in payload:
        extras.append(f"loss={payload['loss']:.2f}")
    if "bankroll" in payload:
        extras.append(f"bankroll={payload['bankroll']:.2f}")
    if "pct" in payload:
        extras.append(f"pct={payload['pct']:.2f}")
    suffix = f" {' '.join(extras)}" if extras else ""
    _LOG.warning("kelly_guard: %s %s%s", code, message, suffix)
    return payload


def check_kelly_ticket(params: dict, bankroll: Optional[float] = None) -> Optional[dict[str, Any]]:
    """Return a violation dict or None. No-op when the env flag is unset.

    The dict is warning metadata. place_order blocks only when
    kelly_config()["enforce_mode"] == "block".
    """
    if not kelly_config()["enforce_orders"]:
        return None

    order_type = str(params.get("type") or "stock").lower()
    if order_type == "stock":
        _LOG.info("kelly_guard: skip stock order")
        return None
    if _is_closing(params):
        return None

    resolved = resolve_kelly_bankroll(bankroll)
    if resolved is None:
        return _violation(
            "KELLY_BANKROLL_UNKNOWN",
            "Kelly guard cannot size without a known bankroll",
        )

    action = str(params.get("action") or "").upper()
    if order_type == "option" and action.startswith("SELL"):
        return _violation(
            "KELLY_UNDEFINED_RISK",
            "naked short option has no max_loss; Kelly guard undefined risk",
        )

    loss = _worst_case_loss(params)
    if loss is None:
        if order_type == "option":
            return None
        return _violation(
            "KELLY_UNDEFINED_RISK",
            "order max_loss cannot be priced; Kelly guard undefined risk",
        )

    cap = resolved * KELLY_MAX_PCT
    pct = (loss / resolved) * 100.0
    if loss > cap + 1e-9:
        return _violation(
            "KELLY_CAP_EXCEEDED",
            f"{loss:.2f} is {pct:.2f}% of bankroll; cap is 2.5%",
            loss=loss,
            bankroll=resolved,
            pct=pct,
        )
    return None
