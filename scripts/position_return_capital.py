"""Execution-linked return capital for open portfolio positions.

IB exposes account-level margin, not native per-position margin.  This module
therefore accepts only two denominator classes:

* exact capital already derivable from the position (max loss or full debit),
* an account ``InitMarginReq`` change whose surrounding position snapshots
  prove that exactly one execution transaction changed the account.

Modeled Reg-T and IB what-if values are estimates.  They are intentionally not
represented by the v2 payload consumed by generic Return %.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Iterable, Optional


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(prefix: str, value: Any) -> str:
    return f"{prefix}-{hashlib.sha256(_canonical(value).encode()).hexdigest()[:24]}"


def _iso(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("missing timestamp")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(value: Any) -> float:
    return datetime.fromisoformat(_iso(value).replace("Z", "+00:00")).timestamp()


def _number(value: Any, *, positive: bool = False) -> float:
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError("invalid numeric value")
    return result


def _int_or_none(value: Any) -> Optional[int]:
    if value in (None, "", 0, "0"):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def normalize_execution(raw: dict[str, Any]) -> dict[str, Any]:
    """Return the lossless identity fields required by lifecycle replay."""
    contract = raw.get("contract") if isinstance(raw.get("contract"), dict) else {}
    exec_id = str(raw.get("exec_id") or raw.get("execId") or "").strip()
    account_id = str(raw.get("account_id") or raw.get("accountId") or raw.get("acctNumber") or "").strip()
    con_id = raw.get("con_id", raw.get("conId", contract.get("conId")))
    if not exec_id or not account_id or con_id in (None, "", 0, "0"):
        raise ValueError("execution requires exec_id, account_id, and con_id")

    side = str(raw.get("side") or "").upper()
    if side not in {"BOT", "BUY", "SLD", "SELL"}:
        raise ValueError("execution side must be buy or sell")
    quantity = _number(raw.get("quantity", raw.get("shares")), positive=True)
    signed_quantity = quantity if side in {"BOT", "BUY"} else -quantity
    currency = str(raw.get("currency") or contract.get("currency") or "").upper().strip()
    multiplier_raw = raw.get("multiplier", contract.get("multiplier") or 1)
    multiplier = _number(multiplier_raw, positive=True)
    order_ref = str(raw.get("order_ref") or raw.get("orderRef") or "").strip()
    perm_id = _int_or_none(raw.get("perm_id", raw.get("permId")))
    if not order_ref and perm_id is None:
        raise ValueError("execution requires order_ref or perm_id")

    price_value = raw.get("price")
    if price_value is None:
        price_value = raw.get("avgPrice", 0)
    return {
        "exec_id": exec_id,
        "account_id": account_id,
        "con_id": int(con_id),
        "side": side,
        "quantity": quantity,
        "signed_quantity": signed_quantity,
        "price": _number(price_value),
        "filled_at": _iso(raw.get("filled_at") or raw.get("time")),
        "perm_id": perm_id,
        "order_ref": order_ref,
        "currency": currency,
        "multiplier": multiplier,
        "symbol": str(raw.get("symbol") or contract.get("symbol") or "").upper(),
        "sec_type": str(raw.get("sec_type") or contract.get("secType") or "").upper(),
        "commission": _number(raw.get("commission") or 0),
    }


def group_execution_transactions(executions: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group real fills by durable broker order identity.

    ``permId`` is preferred because it survives client/order-id changes.  An
    explicit orderRef is the fallback.  A multi-leg fill with neither is
    ambiguous and is rejected by ``normalize_execution``.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for raw in executions:
        item = normalize_execution(raw)
        token = f"perm:{item['perm_id']}" if item["perm_id"] else f"ref:{item['order_ref']}"
        groups.setdefault((item["account_id"], token), []).append(item)
    for items in groups.values():
        items.sort(key=lambda item: (item["filled_at"], item["exec_id"]))
    return sorted(groups.values(), key=lambda items: (items[0]["filled_at"], items[0]["account_id"]))


def _clean_legs(legs: dict[str, float]) -> dict[str, float]:
    return {str(key): round(float(value), 10) for key, value in legs.items() if abs(float(value)) > 1e-9}


def _transaction_vector(items: list[dict[str, Any]]) -> dict[str, float]:
    vector: dict[str, float] = {}
    for item in items:
        key = str(item["con_id"])
        vector[key] = vector.get(key, 0.0) + float(item["signed_quantity"])
    return _clean_legs(vector)


def _strategy_key(account_id: str, con_ids: Iterable[str]) -> str:
    return _digest("strategy", {"account_id": account_id, "con_ids": sorted(str(x) for x in con_ids)})


def replay_transactions(transactions: Iterable[Iterable[dict[str, Any]]]) -> dict[str, Any]:
    """Deterministically replay open/add/reduce/close/reopen position episodes.

    The caller supplies transaction boundaries.  Production uses
    ``group_execution_transactions``.  Duplicate ``(account, exec_id)`` rows
    are ignored, making repeated sync/reconciliation idempotent.
    """
    instances: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    active: dict[str, list[dict[str, Any]]] = {}
    episodes: dict[tuple[str, str], int] = {}
    seen_execs: set[tuple[str, str]] = set()
    errors: list[dict[str, Any]] = []

    normalized_transactions: list[list[dict[str, Any]]] = []
    for raw_items in transactions:
        items: list[dict[str, Any]] = []
        for raw in raw_items:
            try:
                item = raw if "signed_quantity" in raw else normalize_execution(raw)
            except (TypeError, ValueError) as exc:
                errors.append({"reason": str(exc), "execution": raw})
                continue
            identity = (item["account_id"], item["exec_id"])
            if identity in seen_execs:
                continue
            seen_execs.add(identity)
            items.append(item)
        if items:
            items.sort(key=lambda item: (item["filled_at"], item["exec_id"]))
            normalized_transactions.append(items)
    normalized_transactions.sort(key=lambda items: (items[0]["filled_at"], items[0]["account_id"]))

    def append_event(instance: dict[str, Any], kind: str, before: dict[str, float], after: dict[str, float], items: list[dict[str, Any]]) -> dict[str, Any]:
        event_payload = {
            "instance_id": instance["instance_id"],
            "kind": kind,
            "before_legs": before,
            "after_legs": after,
            "exec_ids": [item["exec_id"] for item in items],
        }
        event = {
            "event_id": _digest("event", event_payload),
            "instance_id": instance["instance_id"],
            "account_id": instance["account_id"],
            "kind": kind,
            "effective_at": max(item["filled_at"] for item in items),
            "before_legs": dict(before),
            "after_legs": dict(after),
            "executions": [dict(item) for item in items],
            "transaction_key": _digest("txn", sorted(item["exec_id"] for item in items)),
        }
        events.append(event)
        instance["legs"] = dict(after)
        return event

    def open_instance(account_id: str, legs: dict[str, float], items: list[dict[str, Any]]) -> dict[str, Any]:
        strategy = _strategy_key(account_id, legs)
        episode_key = (account_id, strategy)
        episode = episodes.get(episode_key, 0) + 1
        episodes[episode_key] = episode
        instance = {
            "instance_id": _digest("pi", {
                "account_id": account_id,
                "strategy_key": strategy,
                "episode": episode,
                "opening_exec_ids": sorted(item["exec_id"] for item in items),
            }),
            "account_id": account_id,
            "strategy_key": strategy,
            "episode": episode,
            "opened_at": min(item["filled_at"] for item in items),
            "legs": {},
        }
        instances.append(instance)
        active.setdefault(account_id, []).append(instance)
        append_event(instance, "OPEN", {}, legs, items)
        return instance

    for items in normalized_transactions:
        accounts = {item["account_id"] for item in items}
        if len(accounts) != 1:
            errors.append({"reason": "transaction-account-mismatch", "exec_ids": [i["exec_id"] for i in items]})
            continue
        account_id = next(iter(accounts))
        vector = _transaction_vector(items)
        if not vector:
            continue
        tx_ids = set(vector)
        candidates = [
            instance for instance in active.get(account_id, [])
            if instance["legs"] and tx_ids.issubset(set(instance["legs"]))
        ]
        exact = [instance for instance in candidates if set(instance["legs"]) == tx_ids]
        if len(exact) == 1:
            instance = exact[0]
        elif len(candidates) == 1:
            instance = candidates[0]
        elif not candidates:
            open_instance(account_id, vector, items)
            continue
        else:
            errors.append({"reason": "ambiguous-active-instance", "exec_ids": [i["exec_id"] for i in items]})
            continue

        before = _clean_legs(instance["legs"])
        after_raw = dict(before)
        for key, delta in vector.items():
            after_raw[key] = after_raw.get(key, 0.0) + delta
        after = _clean_legs(after_raw)

        crossed = any(
            before.get(key, 0) * after.get(key, 0) < 0
            for key in set(before) | set(after)
        )
        if crossed:
            append_event(instance, "CLOSE", before, {}, items)
            active[account_id].remove(instance)
            open_instance(account_id, after, items)
            continue
        if not after:
            append_event(instance, "CLOSE", before, {}, items)
            active[account_id].remove(instance)
            continue

        before_abs = sum(abs(value) for value in before.values())
        after_abs = sum(abs(value) for value in after.values())
        kind = "ADD" if after_abs > before_abs else "REDUCE"
        append_event(instance, kind, before, after, items)

    return {"instances": instances, "events": events, "errors": errors}


def _position_map(sample: dict[str, Any]) -> dict[str, float]:
    raw = sample.get("positions")
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError("missing sample positions")
    return _clean_legs({str(key): float(value) for key, value in raw.items()})


def validate_margin_window(
    event: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    executions_in_window: Iterable[dict[str, Any]],
    max_window_seconds: int = 120,
) -> dict[str, Any]:
    """Prove an account margin delta is isolated to one lifecycle event."""
    try:
        if event.get("kind") not in {"OPEN", "ADD", "REDUCE", "CLOSE"}:
            return {"valid": False, "reason": "unsupported-lifecycle-event"}
        account_id = str(event.get("account_id") or "")
        if not account_id or str(before.get("account_id")) != account_id or str(after.get("account_id")) != account_id:
            return {"valid": False, "reason": "account-mismatch"}
        if str(before.get("currency") or "") != str(after.get("currency") or ""):
            return {"valid": False, "reason": "currency-mismatch"}

        before_from = _timestamp(before.get("observed_from"))
        before_through = _timestamp(before.get("observed_through"))
        after_from = _timestamp(after.get("observed_from"))
        after_through = _timestamp(after.get("observed_through"))
        if before_from > before_through or after_from > after_through:
            return {"valid": False, "reason": "invalid-sample-interval"}
        event_execs = [normalize_execution(item) if "signed_quantity" not in item else item for item in event.get("executions", [])]
        if not event_execs:
            return {"valid": False, "reason": "missing-executions"}
        first_fill = min(_timestamp(item["filled_at"]) for item in event_execs)
        last_fill = max(_timestamp(item["filled_at"]) for item in event_execs)
        if not (before_through <= first_fill <= last_fill <= after_from):
            return {"valid": False, "reason": "samples-do-not-bracket-fill"}
        if after_through - before_from > max_window_seconds:
            return {"valid": False, "reason": "stale-samples"}

        window_execs = [normalize_execution(item) if "signed_quantity" not in item else item for item in executions_in_window]
        expected_ids = {(item["account_id"], item["exec_id"]) for item in event_execs}
        actual_ids = {(item["account_id"], item["exec_id"]) for item in window_execs}
        if actual_ids != expected_ids:
            return {"valid": False, "reason": "concurrent-executions"}

        before_positions = _position_map(before)
        after_positions = _position_map(after)
        expected_after = dict(before_positions)
        for con_id, delta in _transaction_vector(event_execs).items():
            expected_after[con_id] = expected_after.get(con_id, 0.0) + delta
        if _clean_legs(expected_after) != after_positions:
            return {"valid": False, "reason": "position-diff-mismatch"}

        margin_before = _number(before.get("initial_margin"))
        margin_after = _number(after.get("initial_margin"))
        delta = margin_after - margin_before
        kind = event["kind"]
        if (kind in {"OPEN", "ADD"} and delta <= 0) or (kind in {"REDUCE", "CLOSE"} and delta >= 0):
            return {"valid": False, "reason": "unexpected-margin-direction"}
        return {
            "valid": True,
            "isolation": "isolated",
            "delta_amount": delta,
            "window_seconds": int(after_through - before_from),
            "before_sample_id": str(before.get("sample_id") or ""),
            "after_sample_id": str(after.get("sample_id") or ""),
            "currency": str(after.get("currency") or ""),
        }
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return {"valid": False, "reason": f"invalid-evidence:{exc}"}


def build_return_capital_payload(
    *,
    amount: float,
    instance: dict[str, Any],
    observation: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Create the only broker-observed payload accepted by the web resolver."""
    try:
        capital = _number(amount, positive=True)
        instance_id = str(instance.get("instance_id") or "").strip()
        account_id = str(instance.get("account_id") or "").strip()
        legs = _clean_legs(instance.get("legs") or {})
        exec_ids = sorted({str(item) for item in observation.get("exec_ids") or [] if str(item)})
        perm_ids = sorted({_int_or_none(item) for item in observation.get("perm_ids") or []} - {None})
        order_refs = sorted({str(item).strip() for item in observation.get("order_refs") or [] if str(item).strip()})
        multipliers = {str(key): _number(value, positive=True) for key, value in (observation.get("multipliers") or {}).items()}
        currency = str(observation.get("currency") or "").upper().strip()
        observed_at = _iso(observation.get("observed_at"))
        before_id = str(observation.get("before_sample_id") or "").strip()
        after_id = str(observation.get("after_sample_id") or "").strip()
        observation_id = str(observation.get("observation_id") or "").strip()
        if not instance_id or not account_id or not legs or not exec_ids or not currency:
            return None
        if not before_id or not after_id or before_id == after_id or not observation_id:
            return None
        if not order_refs and not perm_ids:
            return None
        con_ids = sorted(int(key) for key in legs)
        if set(multipliers) != set(legs):
            return None
    except (TypeError, ValueError):
        return None

    return {
        "version": 2,
        "amount": capital,
        "currency": currency,
        "measurement": {
            "quality": "observed",
            "method": "isolated-account-margin-delta",
            "measured_at": observed_at,
            "observation_id": observation_id,
            "isolation": "isolated",
            "before_sample_id": before_id,
            "after_sample_id": after_id,
            "window_seconds": int(observation.get("window_seconds") or 0),
            "concurrent_exec_ids": [],
        },
        "linkage": {
            "state": "linked",
            "account_id": account_id,
            "position_instance_id": instance_id,
            "con_ids": con_ids,
            "order_refs": order_refs,
            "perm_ids": perm_ids,
            "exec_ids": exec_ids,
            "legs": [
                {"con_id": con_id, "currency": currency, "multiplier": multipliers[str(con_id)]}
                for con_id in con_ids
            ],
        },
    }


def _position_legs(position: dict[str, Any]) -> Optional[dict[str, float]]:
    legs: dict[str, float] = {}
    for leg in position.get("legs") or []:
        if not isinstance(leg, dict):
            return None
        con_id = leg.get("con_id", leg.get("conId"))
        if con_id in (None, "", 0, "0"):
            return None
        quantity = _number(leg.get("contracts"), positive=True)
        sign = -1 if str(leg.get("direction") or "").upper() == "SHORT" else 1
        legs[str(int(con_id))] = legs.get(str(int(con_id)), 0.0) + sign * quantity
    return _clean_legs(legs) or None


def hydrate_return_capital(positions: list[dict[str, Any]], active_bases: list[dict[str, Any]]) -> int:
    """Attach v2 capital only on an exact account + active leg-vector match."""
    attached = 0
    for position in positions:
        account_id = str(position.get("account_id") or "").strip()
        try:
            legs = _position_legs(position)
        except (TypeError, ValueError):
            continue
        if not account_id or not legs:
            continue
        matches = [
            basis for basis in active_bases
            if str(basis.get("account_id") or "") == account_id
            and _clean_legs(basis.get("legs") or {}) == legs
            and isinstance(basis.get("payload"), dict)
            and basis["payload"].get("version") == 2
        ]
        if len(matches) != 1:
            continue
        basis = matches[0]
        position["position_instance_id"] = basis["instance_id"]
        position["return_capital"] = basis["payload"]
        attached += 1
    return attached
