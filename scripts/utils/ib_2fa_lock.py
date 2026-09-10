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
     or ``None`` if free. Start paths refuse to act while any active lease
     exists, including one carrying the same component holder name.
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
push MUST consult this module first." That includes API ensure/restart,
the watchdog, operator control, and launchd setup/manual starts. Docker,
launchd, and IBC automatic restart schedules must remain disabled.

State file path: ``IB_2FA_LOCK_PATH`` env, defaulting to
``/var/lib/radon/ib-lease/ib-2fa-push-lock.json`` on Linux and the per-user
Application Support directory on macOS.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import fcntl

logger = logging.getLogger("radon.ib_2fa_lock")


class GuardLockTimeout(TimeoutError):
    """The canonical lease guard could not be acquired before its deadline."""


# How long a single 2FA push notification is allowed to "own" the lock.
# Must exceed IBKR's backend reconciliation time after a stacked-push
# rejection. 10 minutes is conservative — the user usually approves within
# 30s if they were watching for the push; the extra time covers user-AFK
# scenarios and IBKR backend lag.
DEFAULT_LOCK_TTL_SECS = 600

# /var/lib/radon is owned by the radon user on Hetzner (and writable by
# the systemd-managed FastAPI + ib_watchdog services). macOS launch agents use
# the per-user Application Support directory so every local control plane can
# share the lease without root privileges. Override via env for dev/test.
# Its own subdirectory, not /var/lib/radon itself. The lease is the one piece of
# control-plane state an APP container writes, and the app containers no longer
# get the state directory: /var/lib/radon also holds control-plane-ready, the
# manifest digest and the root deploy transaction journal, and write permission
# on that parent is all an unlink/rename needs. `radon-app-runtime.sh` binds
# only this directory (plus media/) into the container, so a host unit and the
# containerised FastAPI resolve the SAME shared lease. R-381.
DEFAULT_LOCK_DIR = "/var/lib/radon/ib-lease"
DEFAULT_LOCK_PATH = f"{DEFAULT_LOCK_DIR}/ib-2fa-push-lock.json"

# A pending IBKR push always has a live Gateway behind it: the container binds
# the API port before IBC starts waiting for the tap (auth_state=awaiting_2fa is
# observed with port_listening=true for the whole window). So a lease whose
# Gateway port has been shut for longer than this grace is guarding nothing —
# an operator stop, a container crash, or a killed control plane orphaned it —
# and honouring it locks every recovery path out for the rest of the TTL.
# The grace covers container boot, where the lease exists a beat before the
# port binds. 2026-08-25: an admin stop 14s after a restart orphaned a lease
# and blocked Start Gateway for 10 minutes with no container on the host.
GATEWAY_DOWN_GRACE_SECS = 90

# Loopback refuses instantly; the budget only bites on an unreachable
# IB_GATEWAY_HOST, and only for a lease already past its grace.
_PORT_PROBE_TIMEOUT_SECS = 0.35

# Identity of the last lease reported as orphaned. /health re-reads the lease on
# every poll, so the warning is logged once per lease instead of every 2s until
# the next acquire overwrites the file.
_orphan_reported: Optional[tuple[str, float]] = None
# Consecutive port-down observations required before a live lease is treated as
# orphaned. `_gateway_port_listening` swallows every OSError from a 350 ms
# connect — refused, ETIMEDOUT under host load, EMFILE, a misconfigured
# IB_GATEWAY_HOST — so one probe is not evidence a gateway is gone, while a
# compose restart or a slow IBC startup legitimately keeps the port unbound.
# R-210.
ORPHAN_CONFIRM_PROBES = 3
ORPHAN_CONFIRM_INTERVAL_SECS = 0.4
# Identity of a lease whose gateway this process has seen ANSWER. A lease that
# was serving a moment ago is in a restart, not abandoned.
_orphan_seen_up: Optional[tuple[str, float]] = None


def _lock_path() -> Path:
    configured = os.environ.get("IB_2FA_LOCK_PATH")
    if configured:
        return Path(configured)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Radon" / "ib-2fa-push-lock.json"
    return Path(DEFAULT_LOCK_PATH)


def _guard_path() -> Path:
    path = _lock_path()
    return path.with_suffix(path.suffix + ".guard")


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
    """Durably replace the lease file. Caller must hold the guard lock."""
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(lock.to_dict(), fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _write_consumed_marker(path: Path, fingerprint: dict) -> None:
    """Durably replace a consumed-lease marker under its own guard."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(fingerprint, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


@contextmanager
def _guard(*, exclusive: bool, timeout_secs: Optional[float] = None):
    """Serialize lease transactions across processes with ``flock``."""
    path = _guard_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as guard:
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if timeout_secs is None:
            fcntl.flock(guard.fileno(), operation)
        else:
            deadline = time.monotonic() + max(0.0, timeout_secs)
            while True:
                try:
                    fcntl.flock(guard.fileno(), operation | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise GuardLockTimeout(
                            f"2FA lease guard busy after {timeout_secs:g}s"
                        )
                    time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        try:
            yield
        finally:
            fcntl.flock(guard.fileno(), fcntl.LOCK_UN)


def _read_lock_file() -> Optional[PushLock]:
    """Load the lease file. Caller must hold the shared or exclusive guard.

    A recently unreadable lease fails closed for one normal TTL based on its
    mtime. It may represent a push already in flight; treating it as free would
    turn disk corruption into a second IBKR push. An old corrupt file naturally
    ages out through the same expiry rule as a valid crashed-holder lease.
    """
    path = _lock_path()
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            return PushLock.from_dict(json.load(fh))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        try:
            modified_at = path.stat().st_mtime
        except OSError:
            modified_at = time.time()
        logger.error("2FA lock file unreadable (%s); failing closed until TTL", exc)
        return PushLock(
            holder="unreadable-lock-state",
            acquired_at=modified_at,
            expires_at=modified_at + DEFAULT_LOCK_TTL_SECS,
            reason=f"lease file unreadable: {type(exc).__name__}",
        )


def _gateway_port_listening() -> bool:
    """True when something accepts on the IB API port the lease is guarding."""
    host = os.environ.get("IB_GATEWAY_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("IB_GATEWAY_PORT", "4001"))
    except ValueError:
        port = 4001
    try:
        with socket.create_connection((host, port), timeout=_PORT_PROBE_TIMEOUT_SECS):
            return True
    except OSError:
        return False


def reset_orphan_state() -> None:
    """Clear the per-process orphan-confirmation memory (tests, CLI reruns)."""
    global _orphan_reported, _orphan_seen_up
    _orphan_reported = None
    _orphan_seen_up = None


def _is_orphaned(lock: PushLock, now: float) -> bool:
    """True when no push can still be in flight behind ``lock``.

    Age is checked first so the socket probe stays off the hot path: /health
    reads the lease on every poll and a young lease is always honoured.

    Revoking a LIVE lease is the expensive mistake: `acquire_2fa_push_lock`
    then overwrites the holder's record, and the real holder's release becomes
    a no-op because the holder no longer matches — leaving the thief's lease
    for its full TTL and producing exactly the stacked pushes this module
    exists to prevent. So one failed probe is not enough: the port must be
    observed down ORPHAN_CONFIRM_PROBES times in a row, and any successful
    observation for this lease resets the count. R-210.
    """
    global _orphan_reported, _orphan_seen_up
    identity = (lock.holder, lock.acquired_at)

    if now - lock.acquired_at < GATEWAY_DOWN_GRACE_SECS:
        return False

    # Confirm WITHIN this call. A one-shot caller (ib-gateway-control.sh) gets
    # exactly one `_is_orphaned` invocation, so a streak carried across calls
    # would never confirm for it — and it is a legitimate revoker. Probes are
    # only reached once past the grace window and only when the port is down,
    # so the young-lease fast path still opens no socket at all.
    for attempt in range(ORPHAN_CONFIRM_PROBES):
        if _gateway_port_listening():
            _orphan_seen_up = identity
            return False
        if attempt + 1 < ORPHAN_CONFIRM_PROBES:
            time.sleep(ORPHAN_CONFIRM_INTERVAL_SECS)

    if _orphan_seen_up == identity:
        # This process watched the port answer for this very lease a moment
        # ago: a restart, not an abandoned holder. Make the next call re-earn
        # the confirmation from a clean slate rather than revoking now.
        _orphan_seen_up = None
        return False

    if _orphan_reported != identity:
        _orphan_reported = identity
        logger.warning(
            "2FA lease held by %r ignored: Gateway port down for %.0fs "
            "across %d consecutive probes",
            lock.holder,
            now - lock.acquired_at,
            ORPHAN_CONFIRM_PROBES,
        )
    return True


def check_2fa_push_lock(
    now: Optional[float] = None,
    *,
    guard_timeout_secs: Optional[float] = None,
) -> Optional[PushLock]:
    """Return the active lock if one is held, else None.

    An expired lock counts as free — the caller may proceed and will
    typically acquire a fresh lock as part of their restart sequence. So does
    one orphaned by a Gateway that is provably down (see ``_is_orphaned``).
    """
    now = now if now is not None else time.time()
    with _guard(exclusive=False, timeout_secs=guard_timeout_secs):
        lock = _read_lock_file()
    if lock is None:
        return None
    if lock.is_expired(now):
        return None
    if _is_orphaned(lock, now):
        return None
    return lock


def acquire_2fa_push_lock(
    holder: str,
    *,
    ttl_secs: int = DEFAULT_LOCK_TTL_SECS,
    reason: str = "",
    now: Optional[float] = None,
    guard_timeout_secs: Optional[float] = None,
) -> tuple[bool, Optional[PushLock]]:
    """Take the 2FA push lock for ``ttl_secs``. Returns (acquired, current_lock).

    Returns:
      • (True, new_lock)  — lock acquired (was free or expired).
      • (False, holder)   — refused. ``holder`` is the lock currently in
                            force; the caller must NOT fire a restart.

    Holder identity names a component, not a logical operation. Re-acquiring an
    active lease is therefore refused even for the same holder: another call is
    another Gateway cycle and may mint another push.
    """
    now = now if now is not None else time.time()
    with _guard(exclusive=True, timeout_secs=guard_timeout_secs):
        existing = _read_lock_file()
        if (
            existing is not None
            and not existing.is_expired(now)
            and not _is_orphaned(existing, now)
        ):
            return (False, existing)

        lock = PushLock(
            holder=holder,
            acquired_at=now,
            expires_at=now + ttl_secs,
            reason=reason,
        )
        _write_lock_file(lock)
        return (True, lock)


def release_2fa_push_lock(
    *,
    expected_holder: Optional[str] = None,
    guard_timeout_secs: Optional[float] = None,
) -> Optional[PushLock]:
    """Clear the lock file. Returns the lock that was held (if any).

    Idempotent — calling on an already-free lock is a no-op that returns
    None.
    """
    path = _lock_path()
    with _guard(exclusive=True, timeout_secs=guard_timeout_secs):
        existing = _read_lock_file()
        if existing is None:
            return None
        if expected_holder is not None and existing.holder != expected_holder:
            return None
        try:
            path.unlink()
        except FileNotFoundError:
            return None
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return existing


def consume_2fa_push_lock(
    expected_holder: str,
    consumed_path: Path,
    *,
    now: Optional[float] = None,
    guard_timeout_secs: Optional[float] = None,
) -> tuple[bool, Optional[PushLock]]:
    """Atomically verify and mark one exact active lease as consumed.

    The canonical lease guard remains exclusively held from the holder/expiry
    check through the durable consumed-marker replace. A concurrent reset,
    release, or reacquire therefore occurs entirely before or after this CAS,
    never between validation and marking.
    """
    now = now if now is not None else time.time()
    consumed_path = Path(consumed_path)
    consumed_guard = consumed_path.with_suffix(consumed_path.suffix + ".guard")
    # Bounded for the same reason as remaining_lock_secs: the exclusive guard
    # spans _write_lock_file's fsyncs. R-211.
    with _guard(exclusive=True, timeout_secs=guard_timeout_secs):
        existing = _read_lock_file()
        if (
            existing is None
            or existing.is_expired(now)
            or existing.holder != expected_holder
        ):
            return False, existing

        fingerprint = {
            "holder": existing.holder,
            "acquired_at": existing.acquired_at,
            "expires_at": existing.expires_at,
        }
        consumed_guard.parent.mkdir(parents=True, exist_ok=True)
        with consumed_guard.open("a+") as guard:
            fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    prior = json.loads(consumed_path.read_text())
                except (FileNotFoundError, json.JSONDecodeError, OSError):
                    prior = None
                if prior == fingerprint:
                    return False, existing
                _write_consumed_marker(consumed_path, fingerprint)
            finally:
                fcntl.flock(guard.fileno(), fcntl.LOCK_UN)
        return True, existing


def remaining_lock_secs(
    now: Optional[float] = None,
    *,
    guard_timeout_secs: Optional[float] = None,
) -> int:
    """Seconds until the active lock expires, or 0 if free.

    Convenience wrapper for /health and operator endpoints. The deadline is
    forwarded: without it ``_guard`` takes the blocking-flock branch, and the
    guard is held across ``_write_lock_file``'s two fsyncs, so a stalled disk
    hung every /health poll inside the FastAPI handler with no timeout and no
    log line. R-211.
    """
    lock = check_2fa_push_lock(now, guard_timeout_secs=guard_timeout_secs)
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

_CLI_USAGE = (
    "usage: python3 -m scripts.utils.ib_2fa_lock "
    "{check|acquire <holder>|release <holder>|release-any"
    "|consume <holder> <marker-path>}"
)


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
    release_2fa_push_lock(expected_holder=holder)
    print(f"released holder={holder}")
    return 0


def _cli_release_any() -> int:
    """Clear whichever lease is held. For paths that make every lease moot.

    ``release <holder>`` is holder-scoped by design, so it cannot clean up after
    a different component. Stopping the Gateway retires the push of whoever took
    the lease, so that path needs an unconditional clear.
    """
    released = release_2fa_push_lock()
    if released is None:
        print("free")
    else:
        print(f"released holder={released.holder}")
    return 0


def _cli_consume(holder: str, marker_path: str) -> int:
    consumed, current = consume_2fa_push_lock(holder, Path(marker_path))
    if consumed:
        print(f"consumed holder={holder}")
        return 0
    if current is None or current.is_expired():
        print(f"cannot consume holder={holder}: lease is free or expired", file=sys.stderr)
    elif current.holder != holder:
        _print_held(current)
    else:
        print(f"cannot consume holder={holder}: lease already consumed", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    if argv == ["check"]:
        return _cli_check()
    if len(argv) == 2 and argv[0] == "acquire":
        return _cli_acquire(argv[1])
    if len(argv) == 2 and argv[0] == "release":
        return _cli_release(argv[1])
    if argv == ["release-any"]:
        return _cli_release_any()
    if len(argv) == 3 and argv[0] == "consume":
        return _cli_consume(argv[1], argv[2])
    print(_CLI_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
