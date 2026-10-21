"""Bounded REST confirmation of completed Greek-flow minutes.

Only source-confirmed closed buckets are replaced. A REST total is not a
stream delta; current minutes, invalid timestamps and mismatched identities
cannot certify a stream bucket. The upstream gap ledger is never cleared.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from zoneinfo import ZoneInfo

FLOW_FIELDS = (
    "total_delta_flow", "dir_delta_flow", "otm_total_delta_flow", "otm_dir_delta_flow",
    "total_vega_flow", "dir_vega_flow", "otm_total_vega_flow", "otm_dir_vega_flow",
)
MAX_REQUESTS_PER_CYCLE = 10
MAX_ROWS_PER_RESPONSE = 1000
MAX_RESPONSE_BYTES = 1024 * 1024
SETTLEMENT_SECONDS = 60
ET = ZoneInfo("America/New_York")


def minute_timestamp(value) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0 or stamp.second or stamp.microsecond:
            return None
        return stamp.timestamp()
    except (ValueError, OverflowError, OSError, AttributeError):
        return None


def completed_row(channel: str, row: dict, now: float, expiry: str | None = None) -> dict | None:
    if not isinstance(row, dict):
        return None
    family, _, ticker = channel.partition(":")
    if family not in {"greek_flow", "greek_flow_expiry"} or row.get("ticker") != ticker:
        return None
    stamp = minute_timestamp(row.get("timestamp"))
    if stamp is None or stamp + 60 + SETTLEMENT_SECONDS > now or stamp < now - 86400:
        return None
    if family == "greek_flow_expiry":
        if not expiry or row.get("expiry") != expiry or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", expiry):
            return None
    elif row.get("expiry") is not None:
        return None
    value = {"ticker": ticker, "timestamp": datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z")}
    if expiry is not None:
        value["expiry"] = expiry
    try:
        for name in FLOW_FIELDS:
            raw = row.get(name)
            if raw is None or isinstance(raw, bool):
                return None
            number = Decimal(str(raw))
            if not number.is_finite():
                return None
            value[name] = format(number, "f")
        for name in ("transactions", "volume"):
            count = row.get(name)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                return None
            value[name] = count
    except (InvalidOperation, ValueError, TypeError):
        return None
    return value


def pending_requests(channels: tuple[str, ...], snapshot: dict, now: float) -> list[tuple[str, str | None]]:
    result = set()
    for channel in channels:
        family = channel.partition(":")[0]
        if family not in {"greek_flow", "greek_flow_expiry"}:
            continue
        values = snapshot.get("latest", {}).get(channel, {})
        for record in values.values():
            payload = record.get("payload") or {}
            stamp = minute_timestamp(payload.get("timestamp"))
            if stamp is None or stamp + 60 + SETTLEMENT_SECONDS > now or stamp < now - 86400 or record.get("complete") is True and record.get("basis") == "REST-confirmed-completed-minute":
                continue
            if datetime.fromtimestamp(stamp, ET).date() != datetime.fromtimestamp(now, ET).date():
                continue
            expiry = payload.get("expiry") if family == "greek_flow_expiry" else None
            if family == "greek_flow_expiry" and (not isinstance(expiry, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", expiry)):
                continue
            result.add((channel, expiry))
    return sorted(result, key=lambda item: (item[0], item[1] or ""))


def reconcile_cycle(channels: tuple[str, ...], snapshot: dict, now: float, client, emit, *, offset: int = 0, stop=None) -> int:
    """One bounded cycle. Injected dependencies keep fixtures off the network."""
    import json
    tasks = pending_requests(channels, snapshot, now)
    if not tasks:
        return 0
    index = offset % len(tasks)
    tasks = (tasks[index:] + tasks[:index])[:MAX_REQUESTS_PER_CYCLE]
    attempted = 0
    for channel, expiry in tasks:
        if stop is not None and stop.is_set():
            break
        attempted += 1
        ticker = channel.partition(":")[2]
        path = "stock/" + ticker + "/greek-flow" + ("/" + expiry if expiry else "")
        date = datetime.fromtimestamp(now, ET).date().isoformat()
        try:
            response = client.get(path, {"date": date}, priority="background", wait_timeout=0, use_cache=False)
            if not isinstance(response, dict) or len(json.dumps(response, allow_nan=False).encode()) > MAX_RESPONSE_BYTES:
                raise ValueError("Invalid Greek-flow REST envelope")
            rows = response.get("data")
            if not isinstance(rows, list) or len(rows) > MAX_ROWS_PER_RESPONSE:
                raise ValueError("Invalid Greek-flow REST rows")
            # Scope to the requested trading date; prior-session retained rows
            # cannot be mistaken for a current-session confirmation.
            valid = []
            for raw in rows:
                row = completed_row(channel, raw, now, expiry)
                if row is None:
                    continue
                stamp = minute_timestamp(row["timestamp"])
                if datetime.fromtimestamp(stamp, ET).date().isoformat() != date:
                    continue
                valid.append(row)
            if valid:
                emit("__reconcile__", {"channel": channel, "rows": valid}, now)
            else:
                emit("__control__", {"event": "reconciliation_unavailable", "channel": channel}, now)
        except Exception:
            # Never format provider exceptions: URLs may contain credentials.
            emit("__control__", {"event": "reconciliation_unavailable", "channel": channel}, now)
    return attempted
