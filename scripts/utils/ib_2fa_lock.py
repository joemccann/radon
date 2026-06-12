"""Cross-process lock that prevents stacking IBKR 2FA push notifications.

Every IB Gateway restart sends a fresh IBKR Mobile 2FA push to the user's
phone. When multiple independent restart paths fire close together — e.g.
the user runs ``radon restart`` *and* ``ib_watchdog.py`` trips its
``api hang`` detector ~3 minutes later — IBKR's backend can end up with
multiple pending push tokens for the same session. The user taps approve,
and IBKR reports "unsuccessful" on every push because the latest token
invalidates the earlier ones (and vice-versa). The only recovery is to
stop everything, wait for the backend to settle, and approve a single
fresh push.

This module provides a small filesystem-backed lock that every restart
path consults BEFORE issuing a restart:

  1. ``acquire_2fa_push_lock(...)`` is called by the path that is ABOUT
     to fire a 2FA push (i.e. is about to ``docker compose restart`` or
     ``systemctl restart`` the gateway). It writes a JSON record with
     the holder's identity and an expiry timestamp.
  2. ``check_2fa_push_lock()`` returns the active lock if one is held,
     or ``None`` if free. Restart paths refuse to act when a lock is
     held by another holder.
  3. ``release_2fa_push_lock()`` clears the lock — called when an
     authenticated probe confirms login completed, or when the operator
     hits ``POST /ib/reset-backoff``.
  4. The lock auto-expires (default 10 minutes). The window must exceed
     IBKR's backend reconciliation time after a stacked-push rejection;
     10 minutes is a conservative starting point. The lock will not be
     enforced past its TTL even if the holder process crashes without
     releasing.

The lock is *advisory* — any caller that doesn't consult it can still
fire a push. The discipline is "every code path that can spawn a 2FA
push MUST consult this module first." Today that means
``scripts.api.ib_gateway.restart_ib_gateway`` and
``scripts.ib_watchdog.trigger_restart``.

State file path: ``IB_2FA_LOCK_PATH`` env, defaulting to
``/var/lib/radon/ib-2fa-push-lock.json`` on production and a
test-friendly tmp path otherwise.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("radon.ib_2fa_lock")


# How long a single 2FA push notification is allowed to "own" the lock.
# Must exceed IBKR's backend reconciliation time after a stacked-push
# rejection. 10 minutes is conservative — the user usually approves within
# 30s if they were watching for the push; the extra time covers user-AFK
# scenarios and IBKR backend lag.
DEFAULT_LOCK_TTL_SECS = 600

# /var/lib/radon is owned by the radon user on Hetzner (and writable by
# the systemd-managed FastAPI + ib_watchdog services). Override via env
# for dev/test.
DEFAULT_LOCK_PATH = "/var/lib/radon/ib-2fa-push-lock.json"


def _lock_path() -> Path:
    return Path(os.environ.get("IB_2FA_LOCK_PATH", DEFAULT_LOCK_PATH))


@dataclass(frozen=True)
class PushLock:
    """Snapshot of a held 2FA push lock.

    `holder` identifies the path that took the lock — used purely for
    diagnostics so an operator can read the lock file and know which
    component held it.
    """

    holder: str
    acquired_at: float       # epoch seconds
    expires_at: float        # epoch seconds
    reason: str = ""         # human-readable note (e.g. "user-initiated restart")

    def is_expired(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        return now >= self.expires_at

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PushLock":
        return cls(
            holder=str(data.get("holder", "unknown")),
            acquired_at=float(data.get("acquired_at", 0.0)),
            expires_at=float(data.get("expires_at", 0.0)),
            reason=str(data.get("reason", "")),
        )


def _write_lock_file(lock: PushLock) -> None:
    """Persist a lock via temp-file + os.replace() so concurrent readers
    never observe a partial JSON document."""
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        json.dump(lock.to_dict(), fh)
    os.replace(tmp, path)


def _read_lock_file() -> Optional[PushLock]:
    """Load the lock file. Returns None on missing/corrupt.

    Corrupt files are treated as "no lock" — better to allow a restart
    than to wedge the system on a malformed JSON byte.
    """
    path = _lock_path()
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            return PushLock.from_dict(json.load(fh))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("2FA lock file unreadable (%s); treating as free", exc)
        return None


def check_2fa_push_lock(now: Optional[float] = None) -> Optional[PushLock]:
    """Return the active lock if one is held, else None.

    An expired lock counts as free — the caller may proceed and will
    typically acquire a fresh lock as part of their restart sequence.
    """
    lock = _read_lock_file()
    if lock is None:
        return None
    if lock.is_expired(now):
        return None
    return lock


def acquire_2fa_push_lock(
    holder: str,
    *,
    ttl_secs: int = DEFAULT_LOCK_TTL_SECS,
    reason: str = "",
    now: Optional[float] = None,
) -> tuple[bool, Optional[PushLock]]:
    """Take the 2FA push lock for ``ttl_secs``. Returns (acquired, current_lock).

    Returns:
      • (True, new_lock)  — lock acquired (was free or expired).
      • (False, holder)   — refused. ``holder`` is the lock currently in
                            force; the caller must NOT fire a restart.

    The lock file is rewritten on every acquire so subsequent readers see
    the most recent expiry. ``acquired_at`` reflects the new acquisition.

    Idempotency: re-acquiring while you already hold the lock REFRESHES
    the expiry rather than blocking. Two restart paths racing through the
    same holder identifier (e.g. two watchdog cycles) won't deadlock each
    other.
    """
    now = now if now is not None else time.time()
    existing = _read_lock_file()

    if existing is not None and not existing.is_expired(now):
        if existing.holder == holder:
            # Same holder — refresh the lease.
            refreshed = PushLock(
                holder=holder,
                acquired_at=now,
                expires_at=now + ttl_secs,
                reason=reason or existing.reason,
            )
            _write_lock_file(refreshed)
            return (True, refreshed)
        return (False, existing)

    lock = PushLock(
        holder=holder,
        acquired_at=now,
        expires_at=now + ttl_secs,
        reason=reason,
    )
    _write_lock_file(lock)
    return (True, lock)


def release_2fa_push_lock() -> Optional[PushLock]:
    """Clear the lock file. Returns the lock that was held (if any).

    Idempotent — calling on an already-free lock is a no-op that returns
    None.
    """
    path = _lock_path()
    existing = _read_lock_file()
    if existing is None:
        return None
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return existing


def remaining_lock_secs(now: Optional[float] = None) -> int:
    """Seconds until the active lock expires, or 0 if free.

    Convenience wrapper for /health and operator endpoints.
    """
    lock = check_2fa_push_lock(now)
    if lock is None:
        return 0
    now = now if now is not None else time.time()
    return max(0, int(lock.expires_at - now))


# --- CLI entry point ---------------------------------------------------------
#
# Shell control planes that cannot import this module (the radon operator
# CLI in radon-cloud/scripts/operator-radon.sh) consult the lock via
#
#   python3 -m scripts.utils.ib_2fa_lock {check|acquire <holder>|release <holder>}
#
# Exit codes are the contract: 0 = free / acquired / released,
# 1 = held by another holder, 2 = usage error. Success markers print to
# stdout; refusals print holder + remaining seconds to stderr so callers
# can surface them verbatim.

_CLI_USAGE = "usage: python3 -m scripts.utils.ib_2fa_lock {check|acquire <holder>|release <holder>}"


def _print_held(lock: PushLock) -> None:
    remaining = remaining_lock_secs()
    detail = f" reason={lock.reason!r}" if lock.reason else ""
    print(f"held holder={lock.holder} remaining={remaining}s{detail}", file=sys.stderr)


def _cli_check() -> int:
    lock = check_2fa_push_lock()
    if lock is None:
        print("free")
        return 0
    _print_held(lock)
    return 1


def _cli_acquire(holder: str) -> int:
    acquired, lock = acquire_2fa_push_lock(holder, reason=f"cli acquire by {holder}")
    if acquired:
        assert lock is not None
        print(f"acquired holder={lock.holder} ttl={remaining_lock_secs()}s")
        return 0
    assert lock is not None
    _print_held(lock)
    return 1


def _cli_release(holder: str) -> int:
    lock = check_2fa_push_lock()
    if lock is None:
        print("free")
        return 0
    if lock.holder != holder:
        _print_held(lock)
        return 1
    release_2fa_push_lock()
    print(f"released holder={holder}")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["check"]:
        return _cli_check()
    if len(argv) == 2 and argv[0] == "acquire":
        return _cli_acquire(argv[1])
    if len(argv) == 2 and argv[0] == "release":
        return _cli_release(argv[1])
    print(_CLI_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
