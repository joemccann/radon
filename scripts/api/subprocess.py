"""Async subprocess helper for running Python scripts from FastAPI.

Replaces the Node.js spawn pattern in runner.ts with asyncio subprocesses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Union

logger = logging.getLogger("radon.subprocess")

# One FastAPI process owns all Python subprocess delegation. Reject above this
# hard cap instead of building an unbounded coroutine/process queue that can
# exhaust file descriptors, IB client IDs, memory, and upstream quotas.
MAX_CONCURRENT_SUBPROCESSES = max(
    1, min(int(os.environ.get("RADON_MAX_CONCURRENT_SUBPROCESSES", "4")), 16)
)

# REL-023 (R-048): scans hold a slot for their whole lifetime and the preset
# scans run with hour-long timeouts, so a routine scan storm could pin every
# slot and lock out the kill switch — the halt flag would still set while the
# working orders it exists to pull stayed live. The money path therefore gets a
# reserved lane that no other caller can claim.
RESERVED_ORDER_SLOTS = max(
    1, min(int(os.environ.get("RADON_RESERVED_ORDER_SLOTS", "1")), 8)
)
_ORDER_LANE_SCRIPTS = frozenset({
    "ib_place_order.py",
    "ib_order_manage.py",
    "ib_cancel_all.py",
    "ib_execute.py",
})

_active_subprocesses = 0


def _is_order_lane(script: str) -> bool:
    return Path(script).name in _ORDER_LANE_SCRIPTS


def _general_lane_capacity() -> int:
    """Slots a non-order caller may claim. Never zero: a cap too small to
    reserve against still has to admit scans."""
    return max(1, MAX_CONCURRENT_SUBPROCESSES - RESERVED_ORDER_SLOTS)


def _lane_capacity(script: str) -> int:
    if _is_order_lane(script):
        return MAX_CONCURRENT_SUBPROCESSES
    return _general_lane_capacity()


def _claim_subprocess_slot(script: str) -> bool:
    global _active_subprocesses
    # No await between the check and increment: atomic within one event loop.
    capacity = _lane_capacity(script)
    if _active_subprocesses >= capacity:
        logger.warning(
            "Subprocess capacity exhausted for %s (%d active, lane cap %d, hard cap %d)",
            script,
            _active_subprocesses,
            capacity,
            MAX_CONCURRENT_SUBPROCESSES,
        )
        return False
    _active_subprocesses += 1
    return True


def _release_subprocess_slot() -> None:
    global _active_subprocesses
    _active_subprocesses = max(0, _active_subprocesses - 1)

SCRIPTS_DIR = Path(__file__).parent.parent
PROJECT_ROOT = SCRIPTS_DIR.parent


def _extract_error_message(stdout: str, stderr: str, default: str) -> str:
    """Prefer the last meaningful stderr line, then stdout, then the default."""
    for stream in (stderr, stdout):
        lines = [
            l for l in stream.strip().split("\n")
            if l and "warnings.warn(" not in l and "NotOpenSSLWarning" not in l
        ]
        if lines:
            err_msg = lines[-1]
            try:
                parsed = json.loads(err_msg)
                if isinstance(parsed, dict):
                    err_msg = (
                        parsed.get("detail")
                        or parsed.get("message")
                        or parsed.get("error")
                        or err_msg
                    )
            except Exception:
                pass
            if len(err_msg) > 300:
                err_msg = err_msg[:300] + "..."
            return err_msg
    return default


@dataclass
class ScriptResult:
    ok: bool
    data: Optional[Union[dict, list]] = None
    error: Optional[str] = None
    exit_code: Optional[int] = None


@dataclass
class RawScriptResult:
    """Result of a script execution that does NOT parse stdout as JSON.

    Used by the PI command surface: scripts like scanner / discover / evaluate
    emit human-readable progress + report text, and the chat UI renders the
    full stdout. Parsing as JSON would silently drop everything except the
    first object.
    """
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    timed_out: bool = False

    @property
    def error(self) -> Optional[str]:
        """Surface a ScriptResult-shaped `error` so callers can branch
        on result.error without caring whether the result came from
        run_script or run_script_raw."""
        if self.ok:
            return None
        return self.stderr.strip() or f"Script exited with code {self.exit_code}"

    @property
    def data(self) -> dict:
        """RawScriptResult never carries parsed JSON; keep the attribute
        so wrapper code that does `result.data` for an unconditional
        peek doesn't AttributeError."""
        return {}


def _find_json_start(stdout: str) -> int:
    """Return the earliest index of '{' or '[' in stdout, or -1 if neither.

    Used as the FAST path. `_extract_json_payload` below is the smarter
    extractor that scans line-by-line from the end, parses each candidate,
    and returns the first one that round-trips. The fast path remains the
    default because most scripts emit only the result JSON; the smart path
    activates only on parse failure.
    """
    obj_idx = stdout.find("{")
    arr_idx = stdout.find("[")
    candidates = [i for i in (obj_idx, arr_idx) if i != -1]
    return min(candidates) if candidates else -1


def _extract_json_payload(stdout: str) -> Optional[object]:
    """Locate the LAST line in stdout that parses as a complete JSON value.

    Scripts may print progress lines to stdout before the result. A naive
    "find first `{` or `[`" parser breaks when a progress line contains a
    list literal — e.g. `Combo order: 2 legs, ratios=[1, 1]` shipped
    `[1, 1]` as the first JSON-looking thing and tripped on the real
    result as "Extra data: line 2 column 1 (char 7)" (EWY bearish risk
    reversal bug, 2026-05-27).

    Strategy:
      1. Walk stdout lines in REVERSE order.
      2. A parseable dict carrying a "status" key wins immediately — that is
         the result contract for the order paths, so a stray JSON line
         printed AFTER the result can never shadow it (T-012: the previous
         "first parse from the end" rule returned the trailing junk, which
         made a rejected order render as a 200 with a bogus body).
      3. Otherwise remember the LAST line that parses at all (legacy
         behaviour — covers array results and status-less dicts).
      4. If no single line parses, fall back to the slice-from-first-`{`
         strategy via `_find_json_start` (preserving the original
         behaviour for scripts that emit pretty-printed multi-line JSON).
    """
    lines = stdout.splitlines()
    fallback: Optional[object] = None
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0] not in ("{", "["):
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            # Not a single-line JSON — could be a partial line. Keep walking.
            continue
        if isinstance(value, dict) and "status" in value:
            return value
        if fallback is None:
            fallback = value
    if fallback is not None:
        return fallback

    # Fallback: multi-line JSON. Slice from the first '{' or '[' to end.
    start = _find_json_start(stdout)
    if start == -1:
        return None
    return json.loads(stdout[start:])


# R-136: three cancel branches each did `proc.kill(); await proc.wait()` bare.
# A child that exits between the `returncode is None` check and the signal
# raises ProcessLookupError OUT of `except CancelledError`, replacing the
# cancellation and breaking wait_for/TaskGroup semantics; and `wait()` is
# unbounded, so a SIGKILLed child wedged in uninterruptible I/O holds the
# cancelled task and its subprocess slot forever. Scripts are spawned with
# `start_new_session=True` so the whole process group is signalled — only the
# direct child was, which left a script's own children holding IB client ids.
CHILD_REAP_TIMEOUT_SECS = 5.0


async def _terminate_child(proc, *, reap_timeout: float = CHILD_REAP_TIMEOUT_SECS) -> None:
    """Kill a child and its group, bounded, never raising."""
    if proc is None or getattr(proc, "returncode", None) is not None:
        return
    pid = getattr(proc, "pid", None)
    if pid:
        try:
            pgid = os.getpgid(pid)
            # A child that shares our group (spawned without a new session)
            # must never route SIGKILL through killpg — that lands on the
            # API server itself (T-127).
            if pgid != os.getpgid(0):
                os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass
    try:
        await asyncio.wait_for(_shielded_wait(proc), timeout=reap_timeout)
    except (asyncio.TimeoutError, ProcessLookupError, OSError):
        logger.warning("child %s did not reap within %.0fs", pid, reap_timeout)


async def _shielded_wait(proc) -> None:
    """`proc.wait()` that the surrounding cancellation cannot re-cancel."""
    await asyncio.shield(asyncio.ensure_future(proc.wait()))


async def run_script(
    script: str,
    args: Optional[List[str]] = None,
    timeout: float = 30.0,
    cwd: Optional[str] = None,
) -> ScriptResult:
    """Run a Python script as an async subprocess.

    Mirrors the JSON extraction pattern from runner.ts: finds the first '{'
    in stdout and parses from there.

    Args:
        script: Script path relative to scripts/ (e.g. "scanner.py")
        args: CLI arguments
        timeout: Seconds before SIGKILL
        cwd: Working directory (defaults to scripts/)

    Returns:
        ScriptResult with parsed JSON data or error string.
    """
    script_path = SCRIPTS_DIR / script
    if not script_path.exists():
        return ScriptResult(ok=False, error=f"Script not found: {script}")
    if not _claim_subprocess_slot(script):
        return ScriptResult(ok=False, error="Subprocess capacity exhausted")

    cmd = [sys.executable, str(script_path)] + (args or [])
    work_dir = cwd or str(SCRIPTS_DIR)
    proc: Optional[asyncio.subprocess.Process] = None

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            start_new_session=True,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            err_msg = _extract_error_message(
                stdout,
                stderr,
                f"Script exited with code {proc.returncode}",
            )
            logger.warning("Script %s failed (code %d): %s", script, proc.returncode, err_msg)
            return ScriptResult(ok=False, error=err_msg, exit_code=proc.returncode)

        # Extract JSON from stdout (scripts may print progress before JSON).
        # `_extract_json_payload` walks lines in reverse and picks the LAST
        # line that parses as a complete JSON value — so a stray progress
        # print containing a Python list literal (e.g. `ratios=[1, 1]`)
        # doesn't get mistaken for the result.
        payload = _extract_json_payload(stdout)
        if payload is None:
            # Some scripts write to files instead of stdout (rawOutput pattern)
            return ScriptResult(ok=True, data={})
        return ScriptResult(ok=True, data=payload)

    except asyncio.TimeoutError:
        logger.error("Script %s timed out after %.0fs", script, timeout)
        await _terminate_child(proc)
        return ScriptResult(ok=False, error=f"Script timed out after {timeout}s")

    except asyncio.CancelledError:
        await _terminate_child(proc)
        raise

    except json.JSONDecodeError as e:
        logger.error("Script %s returned invalid JSON: %s", script, e)
        return ScriptResult(ok=False, error=f"Invalid JSON output: {e}")

    except Exception as e:
        logger.error("Script %s error: %s", script, e)
        return ScriptResult(ok=False, error=str(e))
    finally:
        _release_subprocess_slot()


async def run_script_raw(
    script: str,
    args: Optional[List[str]] = None,
    timeout: float = 120.0,
    cwd: Optional[str] = None,
) -> RawScriptResult:
    """Run a script and return raw stdout/stderr text (no JSON parsing).

    Mirrors the Node.js `runPythonScript` helper that the PI route used to
    spawn directly. Returns exit code + both streams so the caller can
    decide how to render them.
    """
    script_path = SCRIPTS_DIR / script
    if not script_path.exists():
        return RawScriptResult(
            ok=False, stderr=f"Script not found: {script}", exit_code=None
        )
    if not _claim_subprocess_slot(script):
        return RawScriptResult(
            ok=False, stderr="Subprocess capacity exhausted", exit_code=None
        )

    cmd = [sys.executable, str(script_path)] + (args or [])
    work_dir = cwd or str(SCRIPTS_DIR)
    proc: Optional[asyncio.subprocess.Process] = None

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            start_new_session=True,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return RawScriptResult(
            ok=proc.returncode == 0,
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
        )
    except asyncio.TimeoutError:
        await _terminate_child(proc)
        return RawScriptResult(
            ok=False,
            stderr=f"Script timed out after {timeout}s",
            exit_code=None,
            timed_out=True,
        )
    except asyncio.CancelledError:
        # The slot is released in `finally`; without killing the child first
        # the counter under-counts live processes and the cap stops bounding
        # fds / IB client ids / the reserved order lane.
        await _terminate_child(proc)
        raise
    except Exception as e:
        return RawScriptResult(ok=False, stderr=str(e), exit_code=None)
    finally:
        _release_subprocess_slot()


async def run_module(
    module: str,
    args: Optional[List[str]] = None,
    timeout: float = 30.0,
) -> ScriptResult:
    """Run a Python module (-m) as an async subprocess.

    For scripts invoked as `python3 -m trade_blotter.flex_query --json`.
    """
    cmd = [sys.executable, "-m", module] + (args or [])
    if not _claim_subprocess_slot(module):
        return ScriptResult(ok=False, error="Subprocess capacity exhausted")

    proc: Optional[asyncio.subprocess.Process] = None

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(SCRIPTS_DIR),
            start_new_session=True,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            err_msg = _extract_error_message(
                stdout,
                stderr,
                f"Module exited with code {proc.returncode}",
            )
            return ScriptResult(ok=False, error=err_msg, exit_code=proc.returncode)

        payload = _extract_json_payload(stdout)
        if payload is None:
            return ScriptResult(ok=True, data={})
        return ScriptResult(ok=True, data=payload)

    except asyncio.TimeoutError:
        await _terminate_child(proc)
        return ScriptResult(ok=False, error=f"Module timed out after {timeout}s")

    except asyncio.CancelledError:
        # An orphaned `trade_blotter.flex_query` keeps spending Flex requests
        # against a token already under a 24h-to-168h throttle embargo.
        await _terminate_child(proc)
        raise

    except json.JSONDecodeError as e:
        return ScriptResult(ok=False, error=f"Invalid JSON output: {e}")

    except Exception as e:
        return ScriptResult(ok=False, error=str(e))
    finally:
        _release_subprocess_slot()
