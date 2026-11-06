"""Persisted rate-limit and quota cooldowns plus process-local skips for the model ladder.

A Codex 429 whose reset is days away must not be retried on every 5-minute
oneshot, and an exhausted subscription quota must not be called on every
gateway request. The state file stores provider name and an expiry epoch only.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_DEFAULT_COOLDOWN_S = 12 * 3600
_MAX_COOLDOWN_S = 7 * 24 * 3600
# Quota exhaustion that names no reset: 5m, 10m, 20m ... capped at 6h.
QUOTA_BACKOFF_BASE_S = 300.0
QUOTA_BACKOFF_CAP_S = 6 * 3600.0
_UNIT = r"(?:days?|hours?|hrs?|minutes?|mins?|seconds?|secs?|d|h|m|s)(?![a-z])"
_RELATIVE = re.compile(
    rf"(?:try again in|resets in)\s+((?:\d+\s*{_UNIT}[\s,]*(?:and\s+)?)+)",
    re.IGNORECASE,
)
_DURATION_PART = re.compile(rf"(\d+)\s*({_UNIT})", re.IGNORECASE)
_UNIT_SECONDS = {"d": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0}
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")
_EPOCH_RESET = re.compile(r'"?resets?_at"?\s*[:=]\s*"?(\d{10,13})(?!\d)', re.IGNORECASE)
_QUOTA_MARKERS = (
    "quota",
    "usage limit",
    "usage_limit",
    "credit balance",
    "credit_balance",
    "billing",
    "payment required",
)

_lock = threading.Lock()
_until: dict[str, float] = {}
_quota_streak: dict[str, int] = {}
_process_reason: dict[str, str] = {}


def _now() -> float:
    return time.time()


def clear_process_state() -> None:
    """Drop in-process skips. Tests share a process; oneshots do not."""
    with _lock:
        _until.clear()
        _quota_streak.clear()
        _process_reason.clear()


def state_path(env: Mapping[str, str]) -> Path:
    raw = (env.get("RADON_PROVIDER_COOLDOWN_STATE") or "").strip()
    if raw:
        return Path(raw)
    home = (env.get("HOME") or "").strip() or str(Path.home())
    return Path(home) / ".radon" / "provider-cooldown.json"


def skip_process(provider: str, reason: str) -> None:
    _process_reason[provider] = reason


def process_reason(provider: str) -> str:
    return _process_reason.get(provider, "")


def note(provider: str, seconds: float, env: Mapping[str, str]) -> float:
    """Remember a rate-limit skip; returns the bounded wait. A write failure still skips this process."""
    seconds = min(max(float(seconds), 1.0), _MAX_COOLDOWN_S)
    until = _now() + seconds
    with _lock:
        _until[provider] = max(_until.get(provider, 0.0), until)
    try:
        _persist(provider, until, env)
    except OSError:
        logger.warning("provider cooldown not persisted provider=%s", provider)
    return seconds


def is_quota_exhausted(body: str) -> bool:
    """True when a failure body says the account's quota or plan limit is spent."""
    lowered = (body or "").lower()
    return any(marker in lowered for marker in _QUOTA_MARKERS)


def note_quota(provider: str, reset: float | None, env: Mapping[str, str]) -> float:
    """Back off an exhausted quota until its named reset, else exponentially; returns the wait."""
    if reset is None:
        reset = _next_quota_backoff(provider)
    return note(provider, reset, env)


def _next_quota_backoff(provider: str) -> float:
    with _lock:
        streak = _quota_streak.get(provider, 0)
        _quota_streak[provider] = streak + 1
    return min(QUOTA_BACKOFF_BASE_S * (2 ** min(streak, 32)), QUOTA_BACKOFF_CAP_S)


def clear(provider: str, env: Mapping[str, str]) -> None:
    """Forget a provider's cooldown and backoff streak after it answers."""
    with _lock:
        _until.pop(provider, None)
        _quota_streak.pop(provider, None)
    try:
        if provider in _read(state_path(env)):
            _forget(provider, env)
    except OSError:
        logger.warning("provider cooldown not cleared provider=%s", provider)


def rate_limited(provider: str, env: Mapping[str, str]) -> bool:
    now = _now()
    with _lock:
        if _until.get(provider, 0.0) > now:
            return True
    try:
        until = _read(state_path(env)).get(provider)
    except OSError:
        return False
    if isinstance(until, (int, float)) and not isinstance(until, bool) and float(until) > now:
        with _lock:
            _until[provider] = max(_until.get(provider, 0.0), float(until))
        return True
    return False


def fallback_seconds(env: Mapping[str, str]) -> float:
    raw = (env.get("RADON_PROVIDER_RATE_LIMIT_COOLDOWN_S") or "").strip()
    try:
        value = float(raw) if raw else _DEFAULT_COOLDOWN_S
    except ValueError:
        value = _DEFAULT_COOLDOWN_S
    if value <= 0:
        value = _DEFAULT_COOLDOWN_S
    return min(value, _MAX_COOLDOWN_S)


def reset_seconds(body: str, headers: Mapping[str, str] | None, *, now: float | None = None) -> float | None:
    """Seconds until a parsed reset. None when the body and headers say nothing."""
    moment = _now() if now is None else now
    found: list[float] = []
    header = _retry_after(headers, moment)
    if header is not None and header > 0:
        found.append(header)
    for match in _RELATIVE.finditer(body or ""):
        found.append(_duration_seconds(match.group(1)))
    for match in _EPOCH_RESET.finditer(body or ""):
        epoch = float(match.group(1))
        found.append((epoch / 1000.0 if epoch > 1e12 else epoch) - moment)
    for match in _ISO.finditer(body or ""):
        try:
            stamp = datetime.fromisoformat(match.group(0).replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        found.append(stamp.timestamp() - moment)
    positive = [item for item in found if item > 0]
    return max(positive) if positive else None


def _duration_seconds(text: str) -> float:
    """`24h34m35s` or `1 hour 30 minutes` as seconds."""
    return sum(
        float(amount) * _UNIT_SECONDS[unit[0].lower()]
        for amount, unit in _DURATION_PART.findall(text)
    )


def _retry_after(headers: Mapping[str, str] | None, now: float) -> float | None:
    if not headers:
        return None
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    raw = lowered.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(raw).timestamp() - now
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _read(path: Path) -> dict:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _persist(provider: str, until: float, env: Mapping[str, str]) -> None:
    """Extend-only: a shorter reset never replaces a longer persisted one."""

    def record(data: dict[str, float]) -> None:
        data[provider] = max(data.get(provider, 0.0), until)

    _rewrite(env, record)


def _forget(provider: str, env: Mapping[str, str]) -> None:
    _rewrite(env, lambda data: data.pop(provider, None))


def _rewrite(env: Mapping[str, str], change) -> None:
    """Apply `change` to the live (unexpired) entries under the state file lock."""
    path = state_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "a", encoding="utf-8") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        now = _now()
        data = {
            key: float(value)
            for key, value in _read(path).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) > now
        }
        change(data)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
