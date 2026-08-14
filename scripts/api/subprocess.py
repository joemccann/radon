"""Async subprocess helper for running Python scripts from FastAPI.

Replaces the Node.js spawn pattern in runner.ts with asyncio subprocesses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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
_active_subprocesses = 0


def _claim_subprocess_slot() -> bool:
    global _active_subprocesses
    # No await between the check and increment: atomic within one event loop.
    if _active_subprocesses >= MAX_CONCURRENT_SUBPROCESSES:
        logger.warning(
            "Subprocess capacity exhausted (%d/%d)",
            _active_subprocesses,
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
    if not _claim_subprocess_slot():
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
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return ScriptResult(ok=False, error=f"Script timed out after {timeout}s")

    except asyncio.CancelledError:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
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
    if not _claim_subprocess_slot():
        return RawScriptResult(
            ok=False, stderr="Subprocess capacity exhausted", exit_code=None
        )

    cmd = [sys.executable, str(script_path)] + (args or [])
    work_dir = cwd or str(SCRIPTS_DIR)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
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
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return RawScriptResult(
            ok=False,
            stderr=f"Script timed out after {timeout}s",
            exit_code=None,
            timed_out=True,
        )
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
    if not _claim_subprocess_slot():
        return ScriptResult(ok=False, error="Subprocess capacity exhausted")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(SCRIPTS_DIR),
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
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return ScriptResult(ok=False, error=f"Module timed out after {timeout}s")

    except json.JSONDecodeError as e:
        return ScriptResult(ok=False, error=f"Invalid JSON output: {e}")

    except Exception as e:
        return ScriptResult(ok=False, error=str(e))
    finally:
        _release_subprocess_slot()
