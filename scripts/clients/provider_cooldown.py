"""Persisted rate-limit cooldowns and process-local skips for the model ladder.

A Codex 429 whose reset is days away must not be retried on every 5-minute
oneshot. The state file stores provider name and an expiry epoch only.
"""
from __future__ import annotations

import json
import logging
import os
import re
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
_RELATIVE = re.compile(
    r"(?:try again in|resets in)\s+(\d+)\s*(days?|hours?|minutes?|seconds?|d|h|m|s)\b",
    re.IGNORECASE,
)
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")

_until: dict[str, float] = {}
_process_reason: dict[str, str] = {}


def _now() -> float:
    return time.time()


def clear_process_state() -> None:
    """Drop in-process skips. Tests share a process; oneshots do not."""
    _until.clear()
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


def note(provider: str, seconds: float, env: Mapping[str, str]) -> None:
    """Remember a rate-limit skip. A write failure still skips this process."""
    seconds = min(max(float(seconds), 1.0), _MAX_COOLDOWN_S)
    until = _now() + seconds
    _until[provider] = max(_until.get(provider, 0.0), until)
    try:
        _persist(provider, until, env)
    except OSError:
        logger.warning("provider cooldown not persisted provider=%s", provider)


def rate_limited(provider: str, env: Mapping[str, str]) -> bool:
    now = _now()
    if _until.get(provider, 0.0) > now:
        return True
    try:
        until = _read(state_path(env)).get(provider)
    except OSError:
        return False
    if isinstance(until, (int, float)) and not isinstance(until, bool) and float(until) > now:
        _until[provider] = float(until)
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
        scale = {"d": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0}[match.group(2)[0].lower()]
        found.append(float(match.group(1)) * scale)
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
        data[provider] = until
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
