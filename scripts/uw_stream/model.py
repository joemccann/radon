"""Pure channel semantics. Event messages and snapshots are deliberately different."""
from __future__ import annotations

import copy
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_STATE_KEYS = 2048
MAX_TRADE_TAIL = 100
MAX_PERISCOPE_ROWS = 20_000


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def event_time(value: Any, fallback: float) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        try:
            return iso(value / 1000 if value > 10_000_000_000 else value)
        except (ValueError, OverflowError, OSError):
            pass
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return iso(fallback)


def _ticker(payload: dict, channel: str) -> str:
    return str(payload.get("ticker") or payload.get("underlying_symbol") or payload.get("sym") or channel.partition(":")[2] or "market")[:64]


def _basis(channel: str) -> tuple[str, str]:
    family = channel.partition(":")[0]
    if family == "periscope":
        return "net-market-maker-greek-exposure", "provider-greek-units"
    if family == "risk_reversal_skew":
        return "put-IV-minus-call-IV-at-delta", "IV-fraction-difference; delta-in-whole-percent"
    if family in {"iv_term_structure", "interpolated_iv"}:
        return "provider-volatility-observation", "IV-fraction; implied-move-perc-fraction"
    if family == "futures_blocks":
        return "CME-reported-block; displayed-decimal-prices", "contract-price-and-quantity; no-NBBO-direction-inference"
    if family.startswith("gex"):
        return "provider-spot-exposure", "USD-per-1-percent-move; preserve-field-basis"
    if family.startswith("greek_flow"):
        return "additive-minute-partials", "provider-delta-vega-flow"
    if family == "interval_flow":
        return "latest-five-minute-snapshot", "per-interval-not-daily"
    return "provider-research-observation", "provider-fields"


class Materializer:
    def __init__(self, channels: tuple[str, ...], initial: dict | None = None):
        self.state = {
            "schema_version": 1, "source": "unusual_whales", "status": "connecting",
            "connection": {"connected": False, "connected_at": None, "last_frame_at": None, "gaps": 0, "reconnects": 0},
            "channels": {}, "latest": {},
            "metrics": {"received": 0, "persisted": 0, "invalid": 0, "dropped": 0, "queue_depth": 0},
        }
        if initial and initial.get("schema_version") == 1:
            self.state["latest"] = copy.deepcopy(initial.get("latest") or {})
            self.state["connection"]["gaps"] = int(initial.get("connection", {}).get("gaps") or 0)
        self.pending: dict[tuple[str, str], dict] = {}
        for channel in channels:
            basis, units = _basis(channel)
            self.state["channels"][channel] = {"acknowledged": False, "status": "waiting", "last_event_at": None, "last_received_at": None, "basis": basis, "units": units, "gap_count": 0}
        self.channels = frozenset(channels)

    def connect(self, now: float) -> None:
        self.state["connection"].update(connected=True, connected_at=iso(now), last_frame_at=iso(now))
        self.state["status"] = "connecting"
        for meta in self.state["channels"].values():
            meta["acknowledged"] = False
            meta["status"] = "waiting"

    def disconnect(self, now: float, reason: str = "transport-disconnected") -> None:
        self.state["connection"]["connected"] = False
        self.state["connection"]["reconnects"] += 1
        self.gap(now, reason)
        self.state["status"] = "disconnected"
        self.pending.clear()

    def gap(self, now: float, reason: str) -> None:
        self.state["connection"]["gaps"] += 1
        self.state["last_gap"] = {"at": iso(now), "reason": reason}
        self.state["status"] = "degraded"
        for meta in self.state["channels"].values():
            meta["gap_count"] += 1
            meta["complete"] = False

    def apply(self, channel: str, payload: dict, now: float, ingest_id: int) -> bool:
        if channel not in self.channels or not isinstance(payload, dict):
            self.state["metrics"]["invalid"] += 1
            return False
        self.state["metrics"]["received"] += 1
        self.state["connection"]["last_frame_at"] = iso(now)
        meta = self.state["channels"][channel]
        if "error" in payload:
            meta["acknowledged"] = False
            meta["status"] = "unavailable"
            self.state["status"] = "degraded"
            return True
        if "status" in payload and "response" in payload:
            meta["acknowledged"] = payload["status"] == "ok"
            meta["status"] = "waiting" if meta["acknowledged"] else "unavailable"
            if not meta["acknowledged"]:
                self.state["status"] = "degraded"
            elif self.state["connection"]["connected"] and all(m["acknowledged"] for m in self.state["channels"].values()):
                self.state["status"] = "degraded" if self.state["connection"]["gaps"] else "live"
            return True
        if not meta["acknowledged"]:
            self.state["metrics"]["invalid"] += 1
            return False
        meta["last_received_at"] = iso(now)
        meta["last_event_at"] = event_time(payload.get("timestamp") or payload.get("tape_time") or payload.get("executed_at") or payload.get("created_at"), now)
        meta["status"] = "observed"
        family = channel.partition(":")[0]
        latest = self.state["latest"].setdefault(channel, {})
        ticker = _ticker(payload, channel)
        record = {"payload": copy.deepcopy(payload), "event_time": meta["last_event_at"], "ingested_at": iso(now), "ingest_id": ingest_id}
        if family == "periscope":
            return self._periscope(channel, ticker, payload, record, now)
        if family.startswith("greek_flow"):
            timestamp = payload.get("timestamp")
            if timestamp is None:
                self.state["metrics"]["invalid"] += 1
                return False
            key = ticker + ":" + event_time(timestamp, now) + ":" + str(payload.get("expiry") or "")
            previous_record = latest.get(key, {})
            if previous_record.get("basis") in {"REST-confirmed-completed-minute", "REST-baseline-pending-reconciliation"}:
                # A late partial may already be inside the provider REST sum.
                # Preserve the confirmed total rather than double-count it.
                previous_record["complete"] = False
                previous_record["basis"] = "REST-baseline-pending-reconciliation"
                previous_record["requires_reconciliation"] = True
                previous_record["last_late_partial_at"] = iso(now)
                previous_record["late_partial_count"] = previous_record.get("late_partial_count", 0) + 1
                meta["complete"] = False
                return True
            previous = previous_record.get("payload", {})
            combined = copy.deepcopy(payload)
            for name, value in payload.items():
                if name in {"ticker", "timestamp", "expiry", "date", "tape_time"} or value is None or isinstance(value, bool):
                    continue
                try:
                    number = Decimal(str(value))
                    old = Decimal(str(previous.get(name) or 0))
                    if number.is_finite() and old.is_finite():
                        combined[name] = int(old + number) if isinstance(value, int) else str(old + number)
                except (InvalidOperation, ValueError, TypeError):
                    continue
            record["payload"] = combined
            record["complete"] = meta["gap_count"] == 0
            latest[key] = record
        elif family == "interval_flow":
            if not payload.get("start_time"):
                self.state["metrics"]["invalid"] += 1
                return False
            key = ":".join((ticker, str(payload["start_time"]), str(payload.get("interval_type") or "All")))
            # Repeated snapshots replace; they are not additive trade deltas.
            if key not in latest or record["event_time"] >= latest[key]["event_time"]:
                latest[key] = record
        elif family == "futures_blocks":
            identity = (payload.get("exchange"), payload.get("trade_date"), payload.get("trade_id"))
            if any(value is None for value in identity) or payload.get("action") not in {"new", "update", "delete"}:
                self.state["metrics"]["invalid"] += 1
                return False
            key = ":".join(map(str, identity))
            record["event_time"] = event_time(payload.get("reported_at"), now)
            record["deleted"] = payload["action"] == "delete"
            if key not in latest or record["event_time"] >= latest[key]["event_time"]:
                latest[key] = record
        elif family in {"flow-alerts", "off_lit_trades", "news", "sec"}:
            # Source IDs identify events; ingest IDs only identify local frames.
            source_id = payload.get("id") or payload.get("trade_id") or payload.get("accession_number")
            key = str(source_id) if source_id is not None else "ingest:" + str(ingest_id)
            latest[key] = record
        else:
            key = ticker
            if family in {"gex_strike", "gex_strike_expiry"}:
                key += ":" + str(payload.get("strike") or "unknown") + ":" + str(payload.get("expiry") or "")
            elif family == "risk_reversal_skew":
                delta = payload.get("delta")
                if isinstance(delta, bool) or delta not in {10, 25}:
                    self.state["metrics"]["invalid"] += 1
                    return False
                key += ":" + str(payload.get("expiry") or "unknown") + ":" + str(delta)
            elif family == "iv_term_structure":
                key += ":" + str(payload.get("expiry") or "unknown")
            elif family == "interpolated_iv":
                key += ":" + str(payload.get("days") or "unknown")
            # market_tide already carries its OTM fields. Never add an OTM twin.
            if key not in latest or record["event_time"] >= latest[key]["event_time"]:
                latest[key] = record
        limit = MAX_TRADE_TAIL if family in {"flow-alerts", "off_lit_trades", "news", "sec"} else MAX_STATE_KEYS
        while len(latest) > limit:
            del latest[next(iter(latest))]
            meta["retained_history_limited"] = True
        return True

    def _periscope(self, channel: str, ticker: str, payload: dict, record: dict, now: float) -> bool:
        timestamp, rows, total = payload.get("timestamp"), payload.get("rows"), payload.get("total_rows")
        if timestamp is None or not isinstance(rows, list) or not isinstance(total, int) or isinstance(total, bool) or not 0 <= total <= MAX_PERISCOPE_ROWS or not isinstance(payload.get("has_more"), bool):
            self.state["metrics"]["invalid"] += 1
            return False
        key = (channel, ticker)
        pending = self.pending.get(key)
        if pending is None or pending["timestamp"] != timestamp:
            if pending is not None:
                self.gap(now, "incomplete-periscope-snapshot")
            pending = {"timestamp": timestamp, "total_rows": total, "rows": []}
            self.pending[key] = pending
        if pending["total_rows"] != total or len(pending["rows"]) + len(rows) > total:
            self.pending.pop(key, None)
            self.gap(now, "invalid-periscope-row-count")
            return False
        existing_keys = {(str(row.get("strike")), str(row.get("expiry"))) for row in pending["rows"]}
        new_keys = [(str(row.get("strike")), str(row.get("expiry"))) for row in rows if isinstance(row, dict)]
        if (len(new_keys) != len(rows) or any(not isinstance(row, dict) or row.get("strike") is None or row.get("expiry") is None for row in rows)
                or len(set(new_keys)) != len(new_keys) or existing_keys.intersection(new_keys)):
            self.pending.pop(key, None)
            self.gap(now, "invalid-periscope-row-identity")
            return False
        pending["rows"].extend(rows)
        self.state["channels"][channel]["status"] = "partial"
        if payload["has_more"]:
            return True
        self.pending.pop(key, None)
        if len(pending["rows"]) != total:
            self.gap(now, "incomplete-periscope-row-count")
            return False
        record["payload"] = {"ticker": ticker, **pending, "has_more": False}
        record["complete"] = True
        self.state["latest"][channel][ticker] = record
        self.state["channels"][channel]["status"] = "observed"
        return True

    def reconcile_greek(self, channel: str, rows: list[dict], now: float, ingest_id: int) -> int:
        from .reconcile import completed_row
        if channel not in self.channels or not channel.startswith(("greek_flow:", "greek_flow_expiry:")):
            return 0
        reconciled = 0
        latest = self.state["latest"].setdefault(channel, {})
        for candidate in rows:
            row = completed_row(channel, candidate, now, candidate.get("expiry") if isinstance(candidate, dict) else None)
            if row is None:
                continue
            key = row["ticker"] + ":" + row["timestamp"] + ":" + str(row.get("expiry") or "")
            prior = latest.get(key, {})
            latest[key] = {"payload": row, "event_time": row["timestamp"], "ingested_at": iso(now), "ingest_id": ingest_id,
                           "complete": True, "basis": "REST-confirmed-completed-minute", "rest_observed_at": iso(now),
                           "gap_count_at_reconciliation": self.state["channels"][channel]["gap_count"],
                           "prior_partial_observation": prior.get("payload") if prior.get("basis") is None else prior.get("prior_partial_observation"),
                           "requires_reconciliation": False}
            reconciled += 1
        self.state["metrics"]["reconciled_minutes"] = self.state["metrics"].get("reconciled_minutes", 0) + reconciled
        if reconciled:
            self.state["channels"][channel]["last_rest_reconciled_at"] = iso(now)
            self.state["channels"][channel]["reconciliation_status"] = "completed-minutes-confirmed"
        while len(latest) > MAX_STATE_KEYS:
            del latest[next(iter(latest))]
            self.state["channels"][channel]["retained_history_limited"] = True
        return reconciled

    def snapshot(self, now: float) -> dict:
        result = copy.deepcopy(self.state)
        connected_at = result["connection"].get("connected_at")
        if connected_at and result["connection"]["connected"]:
            joined_at = datetime.fromisoformat(connected_at.replace("Z", "+00:00")).timestamp()
            for meta in result["channels"].values():
                if not meta["acknowledged"] and now - joined_at > 30:
                    meta["status"] = "unavailable"
                    result["status"] = "degraded"
        result["updated_at"] = iso(now)
        result["research_only"] = True
        result["replay_available"] = False
        return result
