"""Shared NVIDIA rate budget for the four fx:nvidia nightly loops.

stdlib only. Invoked as ``python3.13 -I scripts/nvidia_budget.py <cmd>``.
NVIDIA does not publish a limit for integrate.api.nvidia.com; the free-tier
forum figure is 40 rpm. Learn AIMD-style when the response has no
x-ratelimit-* / Retry-After headers.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import random
import sys
import time
from pathlib import Path

DEFAULT_PATH = Path.home() / ".radon" / "nvidia-budget.json"
WINDOW_SECS = 60.0
INITIAL_RPM = 40.0
MIN_RPM = 5.0
MAX_RPM = 120.0
ADD_PER_MIN = 2.0
BACKOFF_BASE = 2.0
BACKOFF_CAP_SECS = 120.0
RECENT_429_SECS = 8.0

_TERMINAL = ("go", "wait", "fallback")


def now() -> float:
    raw = os.environ.get("RADON_NVIDIA_BUDGET_NOW")
    if raw not in (None, ""):
        return float(raw)
    return time.time()


def budget_path() -> Path:
    raw = os.environ.get("RADON_NVIDIA_BUDGET_PATH")
    return Path(raw) if raw else DEFAULT_PATH


def lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


def window_secs() -> float:
    return float(os.environ.get("RADON_NVIDIA_BUDGET_WINDOW_SECS", WINDOW_SECS))


def initial_rpm() -> float:
    return float(os.environ.get("RADON_NVIDIA_BUDGET_INITIAL_RPM", INITIAL_RPM))


def backoff_cap() -> float:
    return float(os.environ.get("RADON_NVIDIA_BUDGET_BACKOFF_CAP_SECS", BACKOFF_CAP_SECS))


def _unit_jitter() -> float:
    raw = os.environ.get("RADON_NVIDIA_BUDGET_JITTER")
    if raw not in (None, ""):
        return max(0.0, min(1.0, float(raw)))
    return random.random()


def _full_jitter(high: float) -> float:
    if high <= 0:
        return 0.0
    return high * _unit_jitter()


def _fresh() -> dict:
    t = now()
    return {
        "learned_rpm": initial_rpm(),
        "cooldown_until": 0.0,
        "requests": [],
        "rate_limits": [],
        "last_429_at": 0.0,
        "last_aimd_at": t,
        "backoff_exp": 0,
    }


def _load_unlocked(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _fresh()
    if not isinstance(data, dict):
        return _fresh()
    state = _fresh()
    state.update({k: data[k] for k in state if k in data})
    try:
        state["learned_rpm"] = float(state["learned_rpm"])
        state["cooldown_until"] = float(state["cooldown_until"])
        state["last_429_at"] = float(state["last_429_at"])
        state["last_aimd_at"] = float(state["last_aimd_at"])
        state["backoff_exp"] = int(state["backoff_exp"])
    except (TypeError, ValueError):
        return _fresh()
    if not isinstance(state["requests"], list):
        state["requests"] = []
    if not isinstance(state["rate_limits"], list):
        state["rate_limits"] = []
    return state


def _dump_atomic(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _prune(state: dict, t: float) -> None:
    cutoff = t - window_secs()
    state["requests"] = [
        row for row in state["requests"]
        if isinstance(row, dict) and float(row.get("t", 0)) >= cutoff
    ]
    state["rate_limits"] = [
        row for row in state["rate_limits"]
        if isinstance(row, dict) and float(row.get("t", 0)) >= cutoff
    ]


def _grow(state: dict, t: float) -> None:
    last_429 = float(state.get("last_429_at") or 0)
    last_aimd = float(state.get("last_aimd_at") or t)
    if last_429 and (t - last_429) < 60.0:
        return
    elapsed = max(0.0, t - last_aimd)
    minutes = elapsed / 60.0
    if minutes <= 0:
        return
    rpm = float(state["learned_rpm"]) + ADD_PER_MIN * minutes
    state["learned_rpm"] = min(MAX_RPM, rpm)
    state["last_aimd_at"] = t


def _cut(state: dict) -> None:
    state["learned_rpm"] = max(MIN_RPM, float(state["learned_rpm"]) * 0.5)


def _with_lock(fn):
    path = budget_path()
    lock = lock_path(path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            state = _load_unlocked(path)
            result = fn(state)
            _dump_atomic(path, state)
            return result
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _max_in_window(state: dict) -> float:
    return max(MIN_RPM, float(state["learned_rpm"])) * (window_secs() / 60.0)


def _wait_needed(state: dict, t: float) -> float:
    wait = 0.0
    cooldown = float(state.get("cooldown_until") or 0)
    if t < cooldown:
        wait = max(wait, cooldown - t)
    last_429 = float(state.get("last_429_at") or 0)
    if last_429 and (t - last_429) < RECENT_429_SECS:
        wait = max(wait, RECENT_429_SECS - (t - last_429))
    cap = _max_in_window(state)
    if cap <= 0:
        return max(wait, window_secs())
    if len(state["requests"]) >= cap:
        oldest = min(float(row["t"]) for row in state["requests"])
        wait = max(wait, (oldest + window_secs()) - t)
    if wait > 0:
        wait += _full_jitter(min(2.0, wait * 0.25))
    return wait


def acquire(loop: str, deadline: float) -> str:
    def _inner(state: dict) -> str:
        t = now()
        _prune(state, t)
        _grow(state, t)
        if deadline <= t:
            return "fallback"
        wait = _wait_needed(state, t)
        if wait <= 0:
            return "go"
        if t + wait >= deadline:
            return "fallback"
        return f"wait {wait:.3f}"

    return _with_lock(_inner)


def record_request(loop: str) -> str:
    def _inner(state: dict) -> str:
        t = now()
        _prune(state, t)
        _grow(state, t)
        state["requests"].append({"t": t, "loop": loop})
        return "ok"

    return _with_lock(_inner)


def record_429(loop: str, retry_after: float | None) -> str:
    def _inner(state: dict) -> str:
        t = now()
        _prune(state, t)
        last_429 = float(state.get("last_429_at") or 0)
        new_burst = (not last_429) or (t - last_429) >= window_secs()
        if new_burst:
            _cut(state)
        state["rate_limits"].append({"t": t, "loop": loop})
        state["last_429_at"] = t
        state["last_aimd_at"] = t
        if retry_after is not None and retry_after >= 0:
            delay = float(retry_after)
            state["backoff_exp"] = 0
        else:
            exp = int(state.get("backoff_exp") or 0)
            high = min(backoff_cap(), BACKOFF_BASE ** max(1, exp + 1))
            delay = _full_jitter(high)
            if delay <= 0:
                delay = high
            state["backoff_exp"] = exp + 1
        state["cooldown_until"] = max(float(state.get("cooldown_until") or 0), t + delay)
        return "ok"

    return _with_lock(_inner)


def status() -> dict:
    def _inner(state: dict) -> dict:
        t = now()
        _prune(state, t)
        _grow(state, t)
        return {
            "learned_rpm": float(state["learned_rpm"]),
            "cooldown_until": float(state["cooldown_until"]),
            "requests": len(state["requests"]),
            "rate_limits": len(state["rate_limits"]),
            "last_429_at": float(state["last_429_at"]),
            "backoff_exp": int(state["backoff_exp"]),
            "window_secs": window_secs(),
            "path": str(budget_path()),
        }

    return _with_lock(_inner)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shared NVIDIA rate budget.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_acq = sub.add_parser("acquire")
    p_acq.add_argument("--loop", required=True)
    p_acq.add_argument("--deadline", required=True, type=float)

    p_req = sub.add_parser("record-request")
    p_req.add_argument("--loop", required=True)

    p_429 = sub.add_parser("record-429")
    p_429.add_argument("--loop", required=True)
    p_429.add_argument("--retry-after", type=float, default=None)

    sub.add_parser("status")

    args = parser.parse_args(argv)
    if args.cmd == "acquire":
        print(acquire(args.loop, args.deadline))
        return 0
    if args.cmd == "record-request":
        print(record_request(args.loop))
        return 0
    if args.cmd == "record-429":
        print(record_429(args.loop, args.retry_after))
        return 0
    if args.cmd == "status":
        json.dump(status(), sys.stdout)
        sys.stdout.write("\n")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
