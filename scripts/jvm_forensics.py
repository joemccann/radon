#!/usr/bin/env python3
"""Bounded JVM forensic capture for the IB Gateway api-hang (DUR-08).

The gateway's Java API thread wedges alive-but-dead several times a day
and has never been diagnosed because no thread dump or GC evidence
exists by the time the watchdog restarts the container. This module is
fired by ``scripts/ib_watchdog.py`` the FIRST time ``is_api_hang`` trips
in an episode (degraded_count 0 -> 1) — minutes BEFORE the 3-cycle
restart ladder recycles the JVM — and snapshots:

  - a thread dump: ``kill -3`` (SIGQUIT) to the in-container java PID;
    the JVM writes the dump to stdout and keeps running
  - ``docker logs --since 5m`` (where that dump lands)
  - ``docker stats --no-stream`` (memory pressure at hang time)
  - ``docker exec ... ps aux`` (in-container process table)

Safety contract:
  - every subprocess call is bounded by ``STEP_TIMEOUT_SECS``
  - the whole capture is bounded by ``TOTAL_BUDGET_SECS``; steps that
    would exceed the deadline are skipped and recorded in the manifest
  - ``capture_jvm_forensics`` NEVER raises — the watchdog restart
    ladder must proceed on schedule regardless of forensic failures
  - retention is capped at ``MAX_CAPTURES_KEPT`` capture directories
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

LOG = logging.getLogger("jvm_forensics")

DEFAULT_CONTAINER = "ib-gateway"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "jvm_forensics"
MAX_CAPTURES_KEPT = 20
TOTAL_BUDGET_SECS = 25.0  # comfortably inside the watchdog's 45s cycle ceiling
STEP_TIMEOUT_SECS = 6.0
THREAD_DUMP_SETTLE_SECS = 3.0
# Match the gateway JVM specifically; plain `pgrep java` is the fallback.
JVM_PGREP_PATTERN = "ibcalpha.ibc.IbcGateway"

_CAPTURE_DIR_RE = re.compile(r"^\d{8}T\d{6}Z$")


@dataclass
class CaptureResult:
    """Outcome of one forensic capture. ``ok`` means the thread-dump path
    (pid -> kill -3 -> docker logs persisted) completed end to end."""

    capture_dir: Optional[Path] = None
    steps: dict = field(default_factory=dict)
    ok: bool = False


class _Deadline:
    def __init__(self, budget_secs: float, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._expires = clock() + budget_secs

    def remaining(self) -> float:
        return max(0.0, self._expires - self._clock())

    def expired(self) -> bool:
        return self.remaining() <= 0.0


class _Capture:
    """Method object holding the shared deadline / runner / manifest state."""

    def __init__(
        self,
        container: str,
        output_dir: Path,
        budget_secs: float,
        max_captures: int,
        runner: Callable,
        sleeper: Callable[[float], None],
        clock: Callable[[], float],
    ) -> None:
        self.container = container
        self.output_dir = output_dir
        self.max_captures = max_captures
        self.runner = runner
        self.sleeper = sleeper
        self.deadline = _Deadline(budget_secs, clock)
        self.result = CaptureResult()

    # -- bounded primitives ---------------------------------------------------

    def _run(self, step: str, cmd: list[str]) -> Optional[str]:
        """Run one bounded subprocess. Returns stdout, or None on any failure
        (recorded in the manifest, never raised)."""
        if self.deadline.expired():
            self.result.steps[step] = "skipped:budget_exhausted"
            return None
        timeout = min(STEP_TIMEOUT_SECS, self.deadline.remaining())
        try:
            proc = self.runner(cmd, capture_output=True, text=True, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — forensics must never raise
            self.result.steps[step] = f"error:{type(exc).__name__}:{exc}"
            return None
        if getattr(proc, "returncode", 1) != 0:
            self.result.steps[step] = f"error:rc={proc.returncode}:{(proc.stderr or '').strip()[:200]}"
            return None
        self.result.steps[step] = "ok"
        return proc.stdout or ""

    def _persist(self, step: str, filename: str, content: Optional[str]) -> None:
        if content is None:
            return
        try:
            (self.result.capture_dir / filename).write_text(content)
        except OSError as exc:
            self.result.steps[step] = f"error:write:{exc}"

    # -- steps ----------------------------------------------------------------

    def _make_capture_dir(self) -> bool:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        try:
            capture_dir = self.output_dir / stamp
            capture_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.result.steps["mkdir"] = f"error:{exc}"
            return False
        self.result.capture_dir = capture_dir
        return True

    def _find_jvm_pid(self) -> Optional[str]:
        out = self._run(
            "find_pid",
            ["docker", "exec", self.container, "pgrep", "-f", JVM_PGREP_PATTERN],
        )
        if not out or not out.strip():
            out = self._run(
                "find_pid_fallback",
                ["docker", "exec", self.container, "pgrep", "java"],
            )
        if not out or not out.strip():
            self.result.steps["find_pid"] = self.result.steps.get("find_pid", "") + ";no_pid"
            return None
        return out.strip().splitlines()[0].strip()

    def _request_thread_dump(self, pid: str) -> bool:
        # `bash -c` so the shell-builtin kill works even if /bin/kill is absent.
        out = self._run(
            "kill_minus_3",
            ["docker", "exec", self.container, "bash", "-c", f"kill -3 {pid}"],
        )
        if out is None:
            return False
        self.sleeper(min(THREAD_DUMP_SETTLE_SECS, self.deadline.remaining()))
        return True

    def _snapshot_logs(self) -> bool:
        out = self._run("docker_logs", ["docker", "logs", "--since", "5m", self.container])
        self._persist("docker_logs", "docker_logs.txt", out)
        return out is not None

    def _snapshot_stats(self) -> None:
        out = self._run("docker_stats", ["docker", "stats", "--no-stream", self.container])
        self._persist("docker_stats", "docker_stats.txt", out)

    def _snapshot_process_table(self) -> None:
        out = self._run("ps_aux", ["docker", "exec", self.container, "ps", "aux"])
        self._persist("ps_aux", "ps_aux.txt", out)

    def _write_manifest(self) -> None:
        manifest = {
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "container": self.container,
            "ok": self.result.ok,
            "steps": self.result.steps,
        }
        try:
            (self.result.capture_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2)
            )
        except OSError as exc:
            LOG.warning("manifest write failed: %s", exc)

    def _prune_old_captures(self) -> None:
        try:
            captures = sorted(
                p for p in self.output_dir.iterdir()
                if p.is_dir() and _CAPTURE_DIR_RE.match(p.name)
            )
            for stale in captures[: max(0, len(captures) - self.max_captures)]:
                shutil.rmtree(stale, ignore_errors=True)
        except OSError as exc:
            LOG.warning("forensics prune failed: %s", exc)

    # -- orchestration ----------------------------------------------------------

    def run(self) -> CaptureResult:
        if not self._make_capture_dir():
            return self.result
        pid = self._find_jvm_pid()
        dump_requested = bool(pid) and self._request_thread_dump(pid)
        logs_persisted = self._snapshot_logs()
        self._snapshot_stats()
        self._snapshot_process_table()
        self.result.ok = dump_requested and logs_persisted
        self._write_manifest()
        self._prune_old_captures()
        return self.result


def capture_jvm_forensics(
    *,
    container: str = DEFAULT_CONTAINER,
    output_dir: Optional[Path] = None,
    budget_secs: float = TOTAL_BUDGET_SECS,
    max_captures: int = MAX_CAPTURES_KEPT,
    runner: Callable = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> CaptureResult:
    """Snapshot JVM forensics for ``container``. Never raises."""
    try:
        result = _Capture(
            container=container,
            output_dir=output_dir or DEFAULT_OUTPUT_DIR,
            budget_secs=budget_secs,
            max_captures=max_captures,
            runner=runner,
            sleeper=sleeper,
            clock=clock,
        ).run()
        LOG.info(
            "jvm forensic capture %s -> %s (steps: %s)",
            "ok" if result.ok else "incomplete",
            result.capture_dir,
            result.steps,
        )
        return result
    except Exception as exc:  # noqa: BLE001 — contract: never propagate
        LOG.warning("jvm forensic capture crashed (non-fatal): %s", exc)
        return CaptureResult(steps={"fatal": f"{type(exc).__name__}:{exc}"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    res = capture_jvm_forensics()
    print(json.dumps({"ok": res.ok, "capture_dir": str(res.capture_dir), "steps": res.steps}, indent=2))
