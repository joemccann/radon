"""Account-wide UW admission shared by Python workers and admitted Next reads.

SQLite is a host-local coordinator, not a cross-host lock. Tokens share one
allowance, so rotating a key never resets reservations. No credential, URL,
response body or arbitrary provider header is persisted. Defaults are a local
operating policy, not an assertion about a subscriber's current entitlement.
"""
from __future__ import annotations

import hashlib
import math
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

DEFAULT_RPM = 120
DEFAULT_DAILY_LIMIT = 40_000
RESERVE_FRACTION = 0.20
MAX_WAIT_SECONDS = 10.0
ET = ZoneInfo("America/New_York")


class AdmissionError(RuntimeError):
    """Admission refused; messages are safe to return without provider data."""


def _database_path() -> Path:
    # Follow the canonical budget path so existing test/state isolation applies
    # to this coordinator too. An override supports an explicit shared volume.
    from utils.uw_budget import BUDGET_PATH
    return Path(os.environ.get("RADON_UW_GOVERNOR_PATH") or BUDGET_PATH.with_name("uw_governor.sqlite3"))


def _day(now: float) -> str:
    dt = datetime.fromtimestamp(now, timezone.utc).astimezone(ET)
    if dt.hour < 20:
        dt -= timedelta(days=1)
    return dt.date().isoformat()


def _next_reset(now: float) -> float:
    dt = datetime.fromtimestamp(now, timezone.utc).astimezone(ET)
    reset = dt.replace(hour=20, minute=0, second=0, microsecond=0)
    if reset.timestamp() <= now:
        reset += timedelta(days=1)
    return reset.timestamp()


def _integer(value, *, minimum: int = 0, maximum: int = 2_000_000_000):
    if isinstance(value, bool) or value is None:
        return None
    try:
        text = str(value).strip()
        if not text.isascii() or not text.isdigit():
            return None
        result = int(text)
        return result if minimum <= result <= maximum else None
    except (TypeError, ValueError, OverflowError):
        return None


def _policy() -> dict:
    verified = os.environ.get("RADON_UW_LIMITS_VERIFIED") == "1"
    raw_rpm = os.environ.get("RADON_UW_REQUESTS_PER_MINUTE", str(DEFAULT_RPM))
    rpm = _integer(raw_rpm, minimum=1, maximum=6000)
    if rpm is None:
        raise AdmissionError("UW minute-limit configuration is invalid")
    if not verified:
        rpm = min(rpm, DEFAULT_RPM)
    period = os.environ.get("RADON_UW_QUOTA_PERIOD", "unknown")
    if period not in {"daily", "monthly", "unknown"}:
        raise AdmissionError("UW quota period configuration is invalid")
    raw_cap = os.environ.get("RADON_UW_DAILY_LIMIT", str(DEFAULT_DAILY_LIMIT)).strip().lower()
    if raw_cap == "unlimited":
        if not verified:
            raise AdmissionError("UW unlimited quota requires a verified account receipt")
        cap = None
    else:
        cap = _integer(raw_cap, minimum=1)
        if cap is None:
            raise AdmissionError("UW quota configuration is invalid")
        if not verified:
            cap = min(cap, DEFAULT_DAILY_LIMIT)
    return {"requests_per_minute": rpm, "daily_limit": cap,
            "quota_period": os.environ.get("RADON_UW_QUOTA_PERIOD", "unknown") if verified else "unknown",
            "limits_verified": verified,
            "limit_source": "verified_operator_policy" if verified else "conservative_local_policy"}


@contextmanager
def _db():
    path = _database_path()
    conn = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=0.25)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, quota_day TEXT NOT NULL, priority TEXT NOT NULL)")
        conn.execute("CREATE INDEX IF NOT EXISTS requests_at ON requests(at)")
        conn.execute("CREATE INDEX IF NOT EXISTS requests_day ON requests(quota_day)")
        conn.execute("CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY CHECK(id=1), embargo REAL NOT NULL DEFAULT 0, observed_at REAL, provider_count INTEGER, provider_cap INTEGER, provider_unlimited INTEGER NOT NULL DEFAULT 0, observed_day TEXT, cutoff INTEGER NOT NULL DEFAULT 0, auth_status INTEGER, token_fingerprint TEXT, reserved_total INTEGER NOT NULL DEFAULT 0, bootstrap_count INTEGER NOT NULL DEFAULT 0, bootstrap_day TEXT)")
        from utils import uw_budget
        seed = uw_budget.used()
        conn.execute("INSERT OR IGNORE INTO policy(id,bootstrap_count,bootstrap_day) VALUES(1,?,?)", (seed, _day(time.time())))
        conn.commit()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        raise AdmissionError("UW admission state is unavailable") from None
    finally:
        if conn is not None:
            conn.close()


def _local_usage(conn, state, day: str) -> int:
    local = conn.execute("SELECT count(*) FROM requests WHERE quota_day=?", (day,)).fetchone()[0]
    if state["bootstrap_day"] == day:
        local += state["bootstrap_count"]
    return local


def _usage(conn, state, day: str, policy: dict) -> int:
    local = _local_usage(conn, state, day)
    if state["provider_count"] is not None and (state["observed_day"] == day or policy["quota_period"] != "daily"):
        pending = max(0, state["reserved_total"] - state["cutoff"])
        return max(local, state["provider_count"] + pending)
    return local


def _effective_cap(policy: dict, state) -> int | None:
    # A numeric provider cap can be monthly (Starter). Never reset that
    # observed count at a guessed daily boundary. Local daily reservations
    # remain separate; exact period/unlimited is operator-verified policy.
    caps = [c for c in (policy["daily_limit"], state["provider_cap"]) if c is not None]
    return min(caps) if caps else None


def reserve(token: str, *, priority: str = "standard", timeout: float = 5.0) -> int:
    """Reserve before EACH actual HTTP attempt; cache hits do not call this.

    Network failures retain their reservation conservatively. Background
    work can consume at most 80% of the minute and local daily policy,
    leaving interactive reads room. Waiting releases the database lock.
    """
    if not isinstance(token, str) or not token.strip():
        raise AdmissionError("UW credential is unavailable")
    if priority not in {"interactive", "standard", "background"}:
        raise AdmissionError("UW admission priority is invalid")
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
        raise AdmissionError("UW admission deadline is invalid")
    policy = _policy()
    deadline = time.monotonic() + min(float(timeout), MAX_WAIT_SECONDS)
    while True:
        now = time.time()
        day = _day(now)
        with _db() as conn:
            state = conn.execute("SELECT * FROM policy WHERE id=1").fetchone()
            used = _usage(conn, state, day, policy)
            # Cap observations can be monthly; a spent provider cap stays
            # closed across ET midnight until a newer provider receipt.
            local_used = _local_usage(conn, state, day)
            # One bounded rollover probe can reconcile a daily count paired
            # with an unspecified-period token cap. A missing receipt cannot
            # unlock repeated requests after an exhausted provider allowance.
            rollover_probe = (policy["quota_period"] == "unknown"
                              and state["observed_day"] != day and local_used == 0)
            if state["provider_cap"] is not None and used >= state["provider_cap"] and not rollover_probe:
                raise AdmissionError("UW provider quota is exhausted")
            factor = 1.0 if priority == "interactive" else 1.0 - RESERVE_FRACTION
            daily_used = max(local_used, used) if policy["quota_period"] == "daily" else local_used
            if policy["daily_limit"] is not None and daily_used >= max(1, int(policy["daily_limit"] * factor)):
                raise AdmissionError("UW request budget is reserved or exhausted")
            if state["provider_cap"] is not None and used >= max(1, int(state["provider_cap"] * factor)) and not rollover_probe:
                raise AdmissionError("UW provider budget is reserved or exhausted")
            minute_limit = max(1, int(policy["requests_per_minute"] * factor))
            minute = conn.execute("SELECT count(*), min(at) FROM requests WHERE at>?", (now - 60,)).fetchone()
            if state["embargo"] <= now and minute[0] < minute_limit:
                request = conn.execute("INSERT INTO requests(at,quota_day,priority) VALUES(?,?,?)", (now, day, priority))
                conn.execute("UPDATE policy SET reserved_total=reserved_total+1 WHERE id=1")
                # At most three days of reservations remain; archive payloads
                # and provider state elsewhere, not in the admission ledger.
                conn.execute("DELETE FROM requests WHERE at<?", (now - 3 * 86400,))
                return request.lastrowid
            wait = max(0.01, state["embargo"] - now, (minute[1] + 60 - now) if minute[0] >= minute_limit else 0)
        remaining = deadline - time.monotonic()
        if remaining <= 0 or wait > remaining:
            raise AdmissionError("UW admission is busy; retry after the current window")
        time.sleep(min(wait, remaining))


def _retry_after(headers: Mapping, now: float) -> float:
    raw = headers.get("retry-after")
    try:
        delay = float(raw)
        if math.isfinite(delay) and delay >= 0:
            return min(delay, 86400.0)
    except (ValueError, TypeError):
        pass
    try:
        parsed = parsedate_to_datetime(str(raw))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return min(86400.0, max(0.0, parsed.timestamp() - now))
    except (ValueError, TypeError, OverflowError):
        return 60.0


def observe(token: str, status: int, headers: Mapping, *, reservation: int | None = None, daily_exhausted: bool = False) -> dict:
    """Reconcile only numeric usage headers; never persist bodies or secrets."""
    now = time.time()
    allowed = {str(k).lower(): v for k, v in headers.items() if str(k).lower() in {"x-uw-token-req-limit", "x-uw-daily-req-count", "retry-after"}}
    cap_raw = str(allowed.get("x-uw-token-req-limit", "")).strip().lower()
    cap = _integer(cap_raw, minimum=1)
    unlimited = cap_raw in {"unlimited", "-1"}
    count = _integer(allowed.get("x-uw-daily-req-count"))
    fingerprint = hashlib.sha256(token.encode()).hexdigest()[:16]
    with _db() as conn:
        state = conn.execute("SELECT * FROM policy WHERE id=1").fetchone()
        if status == 429:
            embargo = _next_reset(now) if daily_exhausted else now + _retry_after(allowed, now)
            conn.execute("UPDATE policy SET embargo=max(embargo,?) WHERE id=1", (embargo,))
        conn.execute("UPDATE policy SET auth_status=?,token_fingerprint=? WHERE id=1", (status if status in (200,401,403) else state["auth_status"], fingerprint))
        if count is not None and (cap is not None or unlimited):
            # Out-of-order responses must not decrease the observed counter
            # within one quota day. A newer low count after rollover can.
            if (state["observed_day"] != _day(now)) or state["provider_count"] is None or count >= state["provider_count"]:
                cutoff = reservation if isinstance(reservation, int) and reservation > 0 else 0
                conn.execute("UPDATE policy SET provider_count=?,provider_cap=?,provider_unlimited=?,observed_day=?,observed_at=?,cutoff=? WHERE id=1", (count, cap, int(unlimited), _day(now), now, cutoff))
    return snapshot(token)


def snapshot(token: str | None = None) -> dict:
    """Safe operator metadata. Missing/malformed state is never healthy."""
    try:
        policy = _policy()
        with _db() as conn:
            state = conn.execute("SELECT * FROM policy WHERE id=1").fetchone()
            now = time.time()
            used = _usage(conn, state, _day(now), policy)
            cap = _effective_cap(policy, state)
            return {**policy, "available": True, "scope": "account", "coordination": "host_shared_sqlite",
                    "effective_limit": cap, "reserved_or_observed": used,
                    "provider_count": state["provider_count"], "provider_cap": state["provider_cap"],
                    "provider_unlimited_observed": bool(state["provider_unlimited"]),
                    "provider_observed_at": state["observed_at"],
                    "embargo_until": state["embargo"], "auth_status": state["auth_status"],
                    "credential_matches": bool(token) and state["token_fingerprint"] == hashlib.sha256(token.encode()).hexdigest()[:16]}
    except AdmissionError:
        return {"available": False, "state_unreadable": True, "scope": "account"}
