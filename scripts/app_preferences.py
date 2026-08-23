#!/usr/bin/env python3
"""Operator-tunable runtime preferences (the /preferences page store).

One registry of declared keys, each with a code default and a HARD band.
Resolution order per key is DB row > environment variable > code default,
with one asymmetry that is the whole point of the module:

  * an environment value outside the band is CLAMPED into it,
  * a DB value outside the band is DISCARDED, never clamped.

A corrupt or hostile ``app_preferences`` row therefore cannot widen a
safety cap such as ``RADON_MAX_ORDER_NOTIONAL``; the worst it can do is
fall back to the deployed environment.

Reads are stale-while-revalidate: ``resolve``/``get_*`` never perform I/O
on the calling thread, so the order placement funnel
(``order_limits.check_order_limits``) stays pure in-process computation on
the uvicorn event loop. The single blocking read is
:func:`refresh_snapshot`, and it runs over the bounded stdlib hrana
transport (``db.hrana_http``) rather than sync libsql, which holds the GIL
and would freeze the API process.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

PrefValue = Union[int, float, bool]

CACHE_TTL_SECONDS: float = 5.0
DB_READ_TIMEOUT_S: float = 2.0
DB_WRITE_TIMEOUT_S: float = 4.0
GROUP_ORDER: Tuple[str, ...] = ("Order Limits", "Scanning")

_WARN_INTERVAL_S = 60.0
_UPDATED_BY_MAX_LEN = 64
_STORE_ERROR_MAX_LEN = 200

_SELECT_SQL = "SELECT key, value, updated_at, updated_by FROM app_preferences"
_EVENT_SQL = (
    "INSERT INTO app_preference_events (key, action, old_value, new_value, actor, at) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
_UPSERT_SQL = (
    "INSERT INTO app_preferences (key, value, updated_at, updated_by) "
    "VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
    "value = excluded.value, updated_at = excluded.updated_at, "
    "updated_by = excluded.updated_by"
)
_DELETE_SQL = "DELETE FROM app_preferences WHERE key = ?"


class PreferenceValidationError(ValueError):
    """Raised by coerce_value / set_value. Never raised by resolve/get_*."""

    def __init__(
        self,
        code: str,
        message: str,
        key: str = "",
        allowed_min: Optional[PrefValue] = None,
        allowed_max: Optional[PrefValue] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.key = key
        self.allowed_min = allowed_min
        self.allowed_max = allowed_max


class PreferenceStoreError(RuntimeError):
    """Raised ONLY by set_value / clear_value when the Turso write fails."""


@dataclass(frozen=True)
class Preference:
    key: str
    label: str
    group: str
    value_type: str
    default: PrefValue
    hard_min: PrefValue
    hard_max: PrefValue
    unit: str
    description: str
    applies_immediately: bool


@dataclass(frozen=True)
class ResolvedPreference:
    preference: Preference
    value: PrefValue
    source: str
    db_rejected: bool
    updated_at: Optional[str]
    updated_by: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        pref = self.preference
        return {
            "key": pref.key,
            "label": pref.label,
            "group": pref.group,
            "value_type": pref.value_type,
            "value": self.value,
            "default": pref.default,
            "hard_min": pref.hard_min,
            "hard_max": pref.hard_max,
            "unit": pref.unit,
            "description": pref.description,
            "applies_immediately": pref.applies_immediately,
            "source": self.source,
            "db_rejected": self.db_rejected,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


REGISTRY: Tuple[Preference, ...] = (
    Preference(
        key="RADON_MAX_ORDER_QTY",
        label="Max contracts per order",
        group="Order Limits",
        value_type="int",
        default=500,
        hard_min=1,
        hard_max=2500,
        unit="contracts",
        description=(
            "Hard ceiling on contracts per options or combo order. The order is "
            "refused at the placement funnel before any IB call."
        ),
        applies_immediately=True,
    ),
    Preference(
        key="RADON_MAX_STOCK_ORDER_QTY",
        label="Max shares per stock order",
        group="Order Limits",
        value_type="int",
        default=10000,
        hard_min=1,
        hard_max=50000,
        unit="shares",
        description=(
            "Hard ceiling on shares per stock order. Shares run larger than "
            "contracts, so this is separate from the contract cap."
        ),
        applies_immediately=True,
    ),
    Preference(
        key="RADON_MAX_ORDER_NOTIONAL",
        label="Max order notional",
        group="Order Limits",
        value_type="float",
        default=250000.0,
        hard_min=1000.0,
        hard_max=1000000.0,
        unit="USD",
        description=(
            "Hard ceiling on dollar notional per order, computed as quantity "
            "times limit price times multiplier. The primary fat finger stop."
        ),
        applies_immediately=True,
    ),
    Preference(
        key="RADON_MAX_COMBO_LOSS_DOLLARS",
        label="Max combo worst-case loss",
        group="Order Limits",
        value_type="float",
        default=10_000_000.0,
        hard_min=10_000.0,
        hard_max=50_000_000.0,
        unit="USD",
        description=(
            "Hard ceiling on a combo order's worst-case loss: assignment for "
            "unpaired shorts, width for paired ones, net of premium. Separate "
            "from notional, which is what the order ticket pays or collects."
        ),
        applies_immediately=True,
    ),
    Preference(
        key="RADON_MAX_ORDERS_PER_MIN",
        label="Max orders per minute",
        group="Order Limits",
        value_type="int",
        default=10,
        hard_min=1,
        hard_max=60,
        unit="orders per minute",
        description=(
            "Accepted order placements allowed per rolling minute. The brake on "
            "a runaway automation loop."
        ),
        applies_immediately=True,
    ),
    Preference(
        key="RADON_WORKFLOW_MAX_ORDERS",
        label="Max orders per workflow run",
        group="Order Limits",
        value_type="int",
        default=3,
        hard_min=1,
        hard_max=18,
        unit="orders",
        description=(
            "Orders a single automated workflow run may place before it is cut "
            "off. The refusal is atomic, so no partial batch is sent."
        ),
        applies_immediately=True,
    ),
    Preference(
        key="RADON_SCANNER_WORKERS",
        label="Watchlist scanner workers",
        group="Scanning",
        value_type="int",
        default=24,
        hard_min=1,
        hard_max=64,
        unit="threads",
        description=(
            "Concurrent worker threads for the watchlist scanner. Higher is "
            "faster but pushes harder on Unusual Whales rate limits."
        ),
        applies_immediately=True,
    ),
    Preference(
        key="RADON_THETA_SCANNER_WORKERS",
        label="Theta harvester workers",
        group="Scanning",
        value_type="int",
        default=24,
        hard_min=1,
        hard_max=64,
        unit="threads",
        description="Concurrent worker threads for the theta harvester scanner.",
        applies_immediately=True,
    ),
    Preference(
        key="RADON_STRENGTH_SCANNER_WORKERS",
        label="Strength confirmation workers",
        group="Scanning",
        value_type="int",
        default=24,
        hard_min=1,
        hard_max=64,
        unit="threads",
        description="Concurrent worker threads for the strength confirmation scanner.",
        applies_immediately=True,
    ),
)

REGISTRY_BY_KEY: Dict[str, Preference] = {pref.key: pref for pref in REGISTRY}

_LOCK = threading.Lock()
_SNAPSHOT: Dict[str, Tuple[str, Optional[str], Optional[str]]] = {}
_SNAPSHOT_AT: float = 0.0
_REFRESHING: bool = False
_BACKGROUND_REFRESH: bool = False
_WRITE_GENERATION: int = 0
_STORE_ERROR: Optional[str] = None
_STORE_CHECKED_AT: Optional[str] = None
_LAST_WARN_AT: Dict[str, float] = {}
_OVERLAY_WRITTEN: Dict[str, str] = {}
_OVERLAY_ORIGINAL: Dict[str, Optional[str]] = {}


def registry() -> Tuple[Preference, ...]:
    return REGISTRY


def get_preference(key: str) -> Optional[Preference]:
    return REGISTRY_BY_KEY.get(key)


# ── parsing / encoding ────────────────────────────────────────────────


def _parse_bool(text: str) -> Optional[bool]:
    token = str(text).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return None


def _parse_text(value_type: str, text: str) -> Optional[PrefValue]:
    if value_type == "bool":
        return _parse_bool(text)
    try:
        number = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number) if value_type == "int" else number


def _encode(pref: Preference, value: PrefValue) -> str:
    if pref.value_type == "bool":
        return "true" if bool(value) else "false"
    if pref.value_type == "int":
        return str(int(value))
    return repr(float(value))


def _in_band(pref: Preference, value: PrefValue) -> bool:
    if pref.value_type == "bool":
        return True
    return pref.hard_min <= value <= pref.hard_max


def _clamp(pref: Preference, value: PrefValue) -> PrefValue:
    if pref.value_type == "bool":
        return bool(value)
    return min(max(value, pref.hard_min), pref.hard_max)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _warn_once(bucket: str, message: str, *args: Any) -> None:
    now = time.monotonic()
    last = _LAST_WARN_AT.get(bucket, 0.0)
    if now - last < _WARN_INTERVAL_S:
        return
    _LAST_WARN_AT[bucket] = now
    logger.warning(message, *args)


# ── transport (lazily imported so the API import graph stays libsql-free) ──


def _hrana_query(sql: str, args: Any = (), timeout: float = DB_READ_TIMEOUT_S) -> List[tuple]:
    try:  # scripts/ on sys.path
        from db.hrana_http import hrana_query
    except ImportError:  # imported as scripts.app_preferences from the repo root
        from scripts.db.hrana_http import hrana_query
    return hrana_query(sql, args, timeout)


def _hrana_execute(sql: str, args: Any = (), timeout: float = DB_WRITE_TIMEOUT_S) -> None:
    try:
        from db.hrana_http import hrana_execute
    except ImportError:
        from scripts.db.hrana_http import hrana_execute
    hrana_execute(sql, args, timeout)


def _db_enabled() -> bool:
    """Under pytest the store is inert: env/default plus whatever
    seed_snapshot_for_tests injected. No threads, no sockets."""
    return not (
        os.environ.get("PYTEST_CURRENT_TEST")
        and os.environ.get("RADON_DB_TEST_WRITE_OK") != "1"
    )


# ── snapshot / refresh ────────────────────────────────────────────────


def refresh_snapshot(timeout: float = DB_READ_TIMEOUT_S) -> bool:
    """The ONLY blocking read. Never raises; returns whether it succeeded.

    A failure keeps the previous snapshot (last known good) and still
    stamps the load time so the next attempt backs off a full TTL.
    """
    global _SNAPSHOT, _SNAPSHOT_AT, _STORE_ERROR, _STORE_CHECKED_AT
    with _LOCK:
        generation_at_start = _WRITE_GENERATION
    try:
        rows = _hrana_query(_SELECT_SQL, (), timeout)
        loaded: Dict[str, Tuple[str, Optional[str], Optional[str]]] = {}
        for row in rows:
            key = str(row[0])
            value = "" if row[1] is None else str(row[1])
            updated_at = None if len(row) < 3 or row[2] is None else str(row[2])
            updated_by = None if len(row) < 4 or row[3] is None else str(row[3])
            loaded[key] = (value, updated_at, updated_by)
    except Exception as exc:  # noqa: BLE001 - the store is best effort by contract
        with _LOCK:
            _SNAPSHOT_AT = time.monotonic()
            _STORE_ERROR = f"{type(exc).__name__}: {exc}"[:_STORE_ERROR_MAX_LEN]
            _STORE_CHECKED_AT = _utcnow_iso()
        _warn_once("refresh", "app_preferences: preference store read failed (%s)", exc)
        return False
    with _LOCK:
        _SNAPSHOT_AT = time.monotonic()
        _STORE_ERROR = None
        _STORE_CHECKED_AT = _utcnow_iso()
        # A save that landed mid-read already holds the newer truth. Adopting
        # this load would silently roll the operator's change back.
        if _WRITE_GENERATION == generation_at_start:
            _SNAPSHOT = loaded
    return True


def _refresh_worker() -> None:
    global _SNAPSHOT_AT, _REFRESHING
    try:
        if refresh_snapshot(DB_READ_TIMEOUT_S):
            apply_env_overlay()
    finally:
        with _LOCK:
            _SNAPSHOT_AT = time.monotonic()
            _REFRESHING = False


def enable_background_refresh(enabled: bool = True) -> None:
    """Opt a LONG-LIVED process into stale-while-revalidate refreshes.

    Off by default. A short-lived process (``ib_place_order.py`` is a fresh
    interpreter per order) would otherwise open a Turso socket per
    invocation and abandon it at exit, which is the documented per-IP
    socket exhaustion failure mode. Those processes resolve from the
    environment overlay ``bootstrap`` published in radon-api instead.
    """
    global _BACKGROUND_REFRESH
    _BACKGROUND_REFRESH = bool(enabled)


def _maybe_refresh_async() -> None:
    global _REFRESHING
    if not _BACKGROUND_REFRESH or not _db_enabled():
        return
    with _LOCK:
        if _REFRESHING or time.monotonic() - _SNAPSHOT_AT <= CACHE_TTL_SECONDS:
            return
        _REFRESHING = True
    try:
        threading.Thread(target=_refresh_worker, daemon=True, name="app-prefs-refresh").start()
    except Exception as exc:  # noqa: BLE001 - resolve() never raises, by contract
        with _LOCK:
            _REFRESHING = False
        _warn_once("spawn", "app_preferences: refresh thread spawn failed (%s)", exc)


def bootstrap(timeout: float = DB_READ_TIMEOUT_S) -> bool:
    """Load the store once and publish it into ``os.environ``.

    Called from the radon-api lifespan so that (a) stored caps are in
    effect before the first request and (b) every subprocess the API
    spawns, including the ``ib_place_order.py`` placement funnel, inherits
    them. Never raises; a dead store leaves env/default in force.
    """
    loaded = refresh_snapshot(timeout)
    apply_env_overlay()
    return loaded


def store_status() -> Dict[str, Any]:
    with _LOCK:
        return {
            "available": _STORE_ERROR is None and _SNAPSHOT_AT > 0.0,
            "error": _STORE_ERROR,
            "checked_at": _STORE_CHECKED_AT,
        }


def seed_snapshot_for_tests(
    rows: Dict[str, str],
    *,
    updated_at: str = "2026-01-01T00:00:00Z",
    updated_by: str = "test",
) -> None:
    global _SNAPSHOT, _SNAPSHOT_AT, _STORE_ERROR, _STORE_CHECKED_AT
    with _LOCK:
        _SNAPSHOT = {key: (str(value), updated_at, updated_by) for key, value in rows.items()}
        _SNAPSHOT_AT = time.monotonic()
        _STORE_ERROR = None
        _STORE_CHECKED_AT = updated_at


def clear_snapshot_for_tests() -> None:
    global _SNAPSHOT, _SNAPSHOT_AT, _STORE_ERROR, _STORE_CHECKED_AT
    with _LOCK:
        _SNAPSHOT = {}
        _SNAPSHOT_AT = 0.0
        _STORE_ERROR = None
        _STORE_CHECKED_AT = None
    _LAST_WARN_AT.clear()
    apply_env_overlay()


# ── resolution ────────────────────────────────────────────────────────


def _resolve_from(
    pref: Preference, row: Optional[Tuple[str, Optional[str], Optional[str]]]
) -> ResolvedPreference:
    db_rejected = False
    if row is not None:
        parsed = _parse_text(pref.value_type, row[0])
        if parsed is not None and _in_band(pref, parsed):
            return ResolvedPreference(
                preference=pref,
                value=parsed,
                source="db",
                db_rejected=False,
                updated_at=row[1],
                updated_by=row[2],
            )
        db_rejected = True
        _warn_once(
            pref.key,
            "app_preferences: ignoring out-of-band DB value for %s",
            pref.key,
        )

    raw_env = os.environ.get(pref.key)
    if raw_env is not None and _OVERLAY_WRITTEN.get(pref.key) == raw_env:
        # Our own export from a previous DB value, not a deployment setting.
        # Reading it back would report source="env" for a row that is gone or
        # has since been rejected, and would pin the discarded value.
        raw_env = None
    if raw_env is not None and str(raw_env).strip():
        parsed_env = _parse_text(pref.value_type, raw_env)
        if parsed_env is not None:
            return ResolvedPreference(
                preference=pref,
                value=_clamp(pref, parsed_env),
                source="env",
                db_rejected=db_rejected,
                updated_at=None,
                updated_by=None,
            )

    return ResolvedPreference(
        preference=pref,
        value=pref.default,
        source="default",
        db_rejected=db_rejected,
        updated_at=None,
        updated_by=None,
    )


def resolve(key: str) -> ResolvedPreference:
    pref = REGISTRY_BY_KEY[key]
    with _LOCK:
        row = _SNAPSHOT.get(key)
    _maybe_refresh_async()
    return _resolve_from(pref, row)


def resolve_all() -> List[ResolvedPreference]:
    with _LOCK:
        snapshot = dict(_SNAPSHOT)
    _maybe_refresh_async()
    return [_resolve_from(pref, snapshot.get(pref.key)) for pref in REGISTRY]


def get_int(key: str) -> int:
    return int(resolve(key).value)


def get_float(key: str) -> float:
    return float(resolve(key).value)


def get_bool(key: str) -> bool:
    return bool(resolve(key).value)


# ── validation ────────────────────────────────────────────────────────


def _coerce_bool(pref: Preference, raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        parsed = _parse_bool(raw)
        if parsed is not None:
            return parsed
    raise PreferenceValidationError(
        "WRONG_TYPE", f"{pref.key} expects a boolean value", pref.key
    )


def _coerce_number(pref: Preference, raw: Any) -> PrefValue:
    wants_int = pref.value_type == "int"
    noun = "an integer" if wants_int else "a numeric"
    if isinstance(raw, bool):
        raise PreferenceValidationError(
            "WRONG_TYPE", f"{pref.key} expects {noun} value", pref.key
        )
    if isinstance(raw, (int, float)):
        number = float(raw)
    elif isinstance(raw, str) and raw.strip():
        try:
            number = float(raw.strip())
        except ValueError:
            raise PreferenceValidationError(
                "WRONG_TYPE", f"{pref.key} expects {noun} value", pref.key
            ) from None
    else:
        raise PreferenceValidationError(
            "WRONG_TYPE", f"{pref.key} expects {noun} value", pref.key
        )
    if wants_int:
        if number != int(number):
            raise PreferenceValidationError(
                "WRONG_TYPE", f"{pref.key} expects an integer value", pref.key
            )
        return int(number)
    return number


def coerce_value(key: str, raw: Any) -> PrefValue:
    pref = REGISTRY_BY_KEY.get(key)
    if pref is None:
        raise PreferenceValidationError(
            "UNKNOWN_PREFERENCE", f"unknown preference key '{key}'", key
        )
    if pref.value_type == "bool":
        return _coerce_bool(pref, raw)
    value = _coerce_number(pref, raw)
    if not _in_band(pref, value):
        raise PreferenceValidationError(
            "OUT_OF_RANGE",
            f"{pref.key} must be between {pref.hard_min} and {pref.hard_max} (got {value})",
            pref.key,
            allowed_min=pref.hard_min,
            allowed_max=pref.hard_max,
        )
    return value


# ── writes ────────────────────────────────────────────────────────────


def _normalized_operator(updated_by: Optional[str]) -> Optional[str]:
    if not isinstance(updated_by, str) or not updated_by:
        return None
    return updated_by[:_UPDATED_BY_MAX_LEN]


def _stored_value(key: str) -> Optional[str]:
    with _LOCK:
        row = _SNAPSHOT.get(key)
    return None if row is None else row[0]


def _record_event(
    key: str, action: str, old_value: Optional[str], new_value: Optional[str], actor: Optional[str]
) -> None:
    """Append the intended change to the immutable event log BEFORE applying it.

    Ordering is deliberate: a failed audit write refuses the mutation, so a
    live risk cap can never move without a record of who moved it. The cost
    is that a mutation failing after its event lands leaves an attempt on
    the log, which is the safe direction to be wrong in.
    """
    try:
        _hrana_execute(
            _EVENT_SQL, (key, action, old_value, new_value, actor, _utcnow_iso()), DB_WRITE_TIMEOUT_S
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as a 503 by the route layer
        raise PreferenceStoreError(f"audit write failed: {type(exc).__name__}: {exc}") from exc


def _mark_store_reachable() -> None:
    global _STORE_ERROR, _STORE_CHECKED_AT, _WRITE_GENERATION
    _STORE_ERROR = None
    _STORE_CHECKED_AT = _utcnow_iso()
    _WRITE_GENERATION += 1


def set_value(key: str, value: Any, updated_by: Optional[str] = None) -> ResolvedPreference:
    """Persist an override. BLOCKING write; raises PreferenceStoreError on failure."""
    coerced = coerce_value(key, value)
    pref = REGISTRY_BY_KEY[key]
    encoded = _encode(pref, coerced)
    updated_at = _utcnow_iso()
    operator = _normalized_operator(updated_by)
    _record_event(key, "set", _stored_value(key), encoded, operator)
    try:
        _hrana_execute(_UPSERT_SQL, (key, encoded, updated_at, operator), DB_WRITE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - surfaced as a 503 by the route layer
        raise PreferenceStoreError(f"{type(exc).__name__}: {exc}") from exc
    with _LOCK:
        _SNAPSHOT[key] = (encoded, updated_at, operator)
        _mark_store_reachable()
    apply_env_overlay()
    return resolve(key)


def clear_value(key: str, updated_by: Optional[str] = None) -> ResolvedPreference:
    """Drop the override. Idempotent: deleting a missing row succeeds."""
    if key not in REGISTRY_BY_KEY:
        raise PreferenceValidationError(
            "UNKNOWN_PREFERENCE", f"unknown preference key '{key}'", key
        )
    operator = _normalized_operator(updated_by)
    _record_event(key, "reset", _stored_value(key), None, operator)
    try:
        _hrana_execute(_DELETE_SQL, (key,), DB_WRITE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        raise PreferenceStoreError(f"{type(exc).__name__}: {exc}") from exc
    with _LOCK:
        _SNAPSHOT.pop(key, None)
        _mark_store_reachable()
    apply_env_overlay()
    return resolve(key)


def apply_env_overlay() -> Dict[str, str]:
    """Export DB-sourced values into os.environ, and unwind stale exports.

    This is what makes the scanner worker counts effective without a
    restart: radon-api reads those names from os.environ at call time and
    subprocess scanners inherit the environment. Only registry keys are
    ever touched.
    """
    global _OVERLAY_WRITTEN
    written: Dict[str, str] = {}
    for pref in REGISTRY:
        with _LOCK:
            row = _SNAPSHOT.get(pref.key)
        resolved = _resolve_from(pref, row)
        if resolved.source != "db":
            continue
        encoded = _encode(pref, resolved.value)
        if pref.key not in _OVERLAY_ORIGINAL:
            _OVERLAY_ORIGINAL[pref.key] = os.environ.get(pref.key)
        os.environ[pref.key] = encoded
        written[pref.key] = encoded

    for key in list(_OVERLAY_WRITTEN):
        if key in written:
            continue
        original = _OVERLAY_ORIGINAL.pop(key, None)
        if original is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original
    _OVERLAY_WRITTEN = written
    return dict(written)
