#!/usr/bin/env python3
"""CTA sync runner with lock, retry, and health ledger tracking."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from fetch_menthorq_cta import cache_path  # noqa: E402
from utils.cta_sync import latest_closed_trading_day  # noqa: E402
from utils.cta_sync_health import (  # noqa: E402
    ARTIFACT_DIR,
    LOCK_DIR,
    classify_sync_error,
    latest_artifacts,
    latest_available_cta_date,
    load_cta_sync_status,
    retry_backoffs_for_error,
    validate_cta_payload,
    write_cta_sync_history,
    write_cta_sync_status,
)

try:
    from db.writer import record_service_health, upsert_menthorq_cta  # noqa: E402
except ImportError:  # pragma: no cover
    record_service_health = None  # type: ignore[assignment]
    upsert_menthorq_cta = None  # type: ignore[assignment]


def _dual_write_cta_to_db(target_date: str, payload: dict[str, Any], finished_at: str) -> None:
    """Phase 2 dual-write — best-effort. Failures don't break the sync."""
    if upsert_menthorq_cta is None or record_service_health is None:
        return
    try:
        upsert_menthorq_cta(target_date, payload, fetched_at=finished_at)
        record_service_health("cta-sync", "ok", finished_at=finished_at)
    except Exception as exc:  # noqa: BLE001 — best-effort, log + continue
        print(f"[cta-sync] db dual-write non-fatal: {exc}", file=sys.stderr)
        try:
            record_service_health(
                "cta-sync",
                "error",
                finished_at=finished_at,
                error={"message": str(exc)},
            )
        except Exception:  # noqa: BLE001
            pass


class CtaSyncLockError(RuntimeError):
    """Raised when a CTA sync lock is already held."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_run_id(started_at: str | None = None) -> str:
    if started_at:
        return started_at.replace("-", "").replace(":", "").replace(".", "").replace("+00:00", "Z")
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _pid_is_alive(pid: int) -> bool:
    """Return True if the given PID is a running process on this host."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        # ProcessLookupError: no such process
        # PermissionError: exists but we lack permission (still alive)
        return isinstance(sys.exc_info()[1], PermissionError)


def _clear_stale_lock(lock_dir: Path) -> bool:
    """Remove lock_dir if held by a dead PID. Returns True if cleared."""
    lock_file = lock_dir / "lock.json"
    if not lock_file.exists():
        shutil.rmtree(lock_dir, ignore_errors=True)
        return True
    try:
        data = json.loads(lock_file.read_text(encoding="utf-8"))
        pid = int(data.get("pid", 0))
    except (OSError, json.JSONDecodeError, ValueError):
        shutil.rmtree(lock_dir, ignore_errors=True)
        return True
    if pid and _pid_is_alive(pid):
        return False
    shutil.rmtree(lock_dir, ignore_errors=True)
    return True


@contextlib.contextmanager
def cta_sync_lock(*, target_date: str, run_id: str, lock_dir: Path = LOCK_DIR):
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "run_id": run_id,
        "target_date": target_date,
        "started_at": utc_now_iso(),
    }
    try:
        lock_dir.mkdir()
    except FileExistsError as exc:
        if not _clear_stale_lock(lock_dir):
            raise CtaSyncLockError("CTA sync lock already held by a live process") from exc
        try:
            lock_dir.mkdir()
        except FileExistsError as exc2:
            raise CtaSyncLockError("CTA sync lock already held") from exc2
    try:
        (lock_dir / "lock.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        yield
    finally:
        shutil.rmtree(lock_dir, ignore_errors=True)


def build_status(
    previous: dict[str, Any] | None,
    *,
    run_id: str,
    state: str,
    target_date: str,
    source: str,
    started_at: str,
    finished_at: str | None,
    latest_available_date: str | None,
    attempt_count: int,
    error: dict[str, str] | None = None,
    cache_path_value: str | None = None,
    artifacts: dict[str, str] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    last_successful_date = previous.get("last_successful_date") if previous else None
    last_successful_at = previous.get("last_successful_at") if previous else None
    last_cache_path = previous.get("last_cache_path") if previous else None

    if state == "healthy" and cache_path_value:
        last_successful_date = target_date
        last_successful_at = finished_at
        last_cache_path = cache_path_value

    return {
        "service": "cta-sync",
        "run_id": run_id,
        "state": state,
        "target_date": target_date,
        "latest_available_date": latest_available_date,
        "last_attempt_started_at": started_at,
        "last_attempt_finished_at": finished_at,
        "last_successful_date": last_successful_date,
        "last_successful_at": last_successful_at,
        "last_cache_path": last_cache_path,
        "attempt_count": attempt_count,
        "last_error": error,
        "last_run_source": source,
        "artifacts": artifacts or {},
        "message": message,
    }


def write_final_status(status: dict[str, Any]) -> None:
    write_cta_sync_status(status)
    write_cta_sync_history(status)


def run_cta_sync(
    *,
    target_date: str | None = None,
    source: str = "schedule",
    force: bool = False,
    sleep_fn: Any = time.sleep,
) -> int:
    started_at = utc_now_iso()
    run_id = build_run_id(started_at)
    previous = load_cta_sync_status()
    latest_before = latest_available_cta_date()
    target = target_date or latest_closed_trading_day()
    target_cache = cache_path(target)

    if target_cache.exists() and not force:
        finished_at = utc_now_iso()
        status = build_status(
            previous,
            run_id=run_id,
            state="healthy",
            target_date=target,
            source=source,
            started_at=started_at,
            finished_at=finished_at,
            latest_available_date=target,
            attempt_count=0,
            cache_path_value=str(target_cache),
            message="CTA cache already fresh for target date",
        )
        write_final_status(status)
        try:
            cached_payload = json.loads(target_cache.read_text())
            _dual_write_cta_to_db(target, cached_payload, finished_at)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[cta-sync] dual-write skipped (cache unreadable): {exc}", file=sys.stderr)
        print(f"CTA cache already exists for {target} — skipping", file=sys.stderr)
        return 0

    try:
        with cta_sync_lock(target_date=target, run_id=run_id):
            write_cta_sync_status(
                build_status(
                    previous,
                    run_id=run_id,
                    state="syncing",
                    target_date=target,
                    source=source,
                    started_at=started_at,
                    finished_at=None,
                    latest_available_date=latest_before,
                    attempt_count=0,
                    message="CTA sync in progress",
                )
            )

            attempt_count = 1
            wait_seconds = 0
            error_type: str | None = None
            error_message = "CTA sync failed without stderr output"
            artifact_payload: dict[str, str] = {}

            while True:
                if wait_seconds > 0:
                    sleep_fn(wait_seconds)

                attempt_artifact_dir = ARTIFACT_DIR / f"cta-sync-{run_id}" / f"attempt-{attempt_count:02d}"
                attempt_artifact_dir.mkdir(parents=True, exist_ok=True)

                # Run fetch in a subprocess to guarantee asyncio isolation.
                # Playwright sync API conflicts with stale event loops from
                # previous attempts when retried in-process.
                fetch_cmd = [
                    sys.executable,
                    str(SCRIPT_DIR / "fetch_menthorq_cta.py"),
                    "--json",
                    "--date", target,
                ]
                if force:
                    fetch_cmd.append("--force")
                fetch_result = subprocess.run(
                    fetch_cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=str(SCRIPT_DIR.parent),
                    env={**os.environ, "MENTHORQ_ARTIFACT_DIR": str(attempt_artifact_dir)},
                )
                captured_stdout = fetch_result.stdout
                captured_stderr = fetch_result.stderr
                if captured_stderr:
                    print(captured_stderr, end="", file=sys.stderr)

                # Parse payload from JSON stdout
                payload = None
                if fetch_result.returncode == 0 and captured_stdout.strip():
                    try:
                        payload = json.loads(captured_stdout)
                    except json.JSONDecodeError:
                        pass

                valid, reason = validate_cta_payload(payload, target)
                artifact_payload = latest_artifacts(artifact_dir=attempt_artifact_dir)

                if valid:
                    finished_at = utc_now_iso()
                    status = build_status(
                        previous,
                        run_id=run_id,
                        state="healthy",
                        target_date=target,
                        source=source,
                        started_at=started_at,
                        finished_at=finished_at,
                        latest_available_date=target,
                        attempt_count=attempt_count,
                        cache_path_value=str(target_cache),
                        artifacts=artifact_payload,
                        message="CTA sync completed successfully",
                    )
                    write_final_status(status)
                    _dual_write_cta_to_db(target, payload, finished_at)
                    return 0

                error_text = captured_stderr or captured_stdout or reason or "CTA sync returned invalid payload"
                error_type, error_message = classify_sync_error(error_text)
                backoffs = retry_backoffs_for_error(error_type)
                if attempt_count >= len(backoffs):
                    break

                wait_seconds = backoffs[attempt_count]
                attempt_count += 1

            finished_at = utc_now_iso()
            status = build_status(
                previous,
                run_id=run_id,
                state="degraded",
                target_date=target,
                source=source,
                started_at=started_at,
                finished_at=finished_at,
                latest_available_date=latest_available_cta_date(),
                attempt_count=attempt_count,
                error={"type": error_type or "unexpected_failure", "message": error_message},
                artifacts=artifact_payload,
                message=f"CTA sync failed after {attempt_count} attempt(s)",
            )
            write_final_status(status)
            return 1
    except CtaSyncLockError:
        finished_at = utc_now_iso()
        status = build_status(
            previous,
            run_id=run_id,
            state="syncing",
            target_date=target,
            source=source,
            started_at=started_at,
            finished_at=finished_at,
            latest_available_date=latest_before,
            attempt_count=0,
            message="CTA sync skipped because another run is in progress",
        )
        write_cta_sync_status(status)
        print("CTA sync already running — skipping duplicate trigger", file=sys.stderr)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CTA sync with health tracking")
    parser.add_argument("--target-date", dest="target_date", help="Override target date (YYYY-MM-DD)")
    parser.add_argument("--source", default=os.environ.get("RADON_CTA_SYNC_SOURCE"))
    parser.add_argument("--force", action="store_true", help="Force re-fetch even when cache exists")

    # Backward-compatible CLI knobs from the earlier wrapper path.
    parser.add_argument("--python-bin", help=argparse.SUPPRESS)
    parser.add_argument("--trigger", help=argparse.SUPPRESS)
    parser.add_argument("--max-attempts", help=argparse.SUPPRESS)
    parser.add_argument("--backoff-seconds", help=argparse.SUPPRESS)

    args = parser.parse_args()
    source = args.source or args.trigger or os.environ.get("CTA_SYNC_TRIGGER", "manual")
    return run_cta_sync(target_date=args.target_date, source=source, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
