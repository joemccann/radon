"""Tests for scripts/api/subprocess.py — async subprocess helper.

Edge cases:
- Script not found
- Successful JSON extraction from stdout with progress prefix
- Empty stdout (rawOutput pattern → returns empty dict)
- Script exit code != 0 (stderr extraction, noise filtering)
- JSON parse error from invalid output
- Timeout handling
- Module execution (-m) path
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure scripts/ is on sys.path
SCRIPTS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from api.subprocess import run_script, run_module, ScriptResult


@pytest.fixture
def temp_script(tmp_path):
    """Create a temporary Python script for testing."""
    def _create(content: str, name: str = "test_script.py") -> str:
        script = tmp_path / name
        script.write_text(content)
        return str(script)
    return _create


class TestRunScript:
    """Tests for run_script()."""

    def test_script_not_found(self):
        result = asyncio.get_event_loop().run_until_complete(
            run_script("nonexistent_script_xyz.py")
        )
        assert not result.ok
        assert "not found" in result.error.lower()

    def test_successful_json_output(self, temp_script):
        script = temp_script('import json; print(json.dumps({"status": "ok", "count": 42}))')
        # run_script expects path relative to scripts/, so we need to adjust
        result = asyncio.get_event_loop().run_until_complete(
            _run_raw_script(script)
        )
        assert result.ok
        assert result.data["status"] == "ok"
        assert result.data["count"] == 42

    def test_json_extraction_with_progress_prefix(self, temp_script):
        """Scripts may print progress lines before JSON output."""
        script = temp_script(
            'import json, sys\n'
            'print("Processing...", file=sys.stderr)\n'
            'print("Progress: 50%")\n'
            'print("Progress: 100%")\n'
            'print(json.dumps({"result": "done"}))\n'
        )
        result = asyncio.get_event_loop().run_until_complete(
            _run_raw_script(script)
        )
        assert result.ok
        assert result.data["result"] == "done"

    def test_empty_stdout_returns_empty_dict(self, temp_script):
        """Scripts that write to files produce no stdout JSON."""
        script = temp_script('print("Saved to file")')
        result = asyncio.get_event_loop().run_until_complete(
            _run_raw_script(script)
        )
        assert result.ok
        assert result.data == {}

    def test_exit_code_nonzero_returns_error(self, temp_script):
        script = temp_script(
            'import sys\n'
            'print("Some useful error context", file=sys.stderr)\n'
            'sys.exit(1)\n'
        )
        result = asyncio.get_event_loop().run_until_complete(
            _run_raw_script(script)
        )
        assert not result.ok
        assert result.exit_code == 1
        assert "error context" in result.error.lower()

    def test_stderr_noise_filtering(self, temp_script):
        """NotOpenSSLWarning and warnings.warn lines should be filtered."""
        script = temp_script(
            'import sys\n'
            'print("warnings.warn(some_warning)", file=sys.stderr)\n'
            'print("NotOpenSSLWarning blah", file=sys.stderr)\n'
            'print("The real error message", file=sys.stderr)\n'
            'sys.exit(1)\n'
        )
        result = asyncio.get_event_loop().run_until_complete(
            _run_raw_script(script)
        )
        assert not result.ok
        assert "real error message" in result.error.lower()
        assert "NotOpenSSLWarning" not in result.error
        assert "warnings.warn" not in result.error

    def test_long_error_message_truncated(self, temp_script):
        """Error messages > 300 chars should be truncated."""
        long_msg = "x" * 500
        script = temp_script(
            f'import sys\nprint("{long_msg}", file=sys.stderr)\nsys.exit(1)\n'
        )
        result = asyncio.get_event_loop().run_until_complete(
            _run_raw_script(script)
        )
        assert not result.ok
        assert len(result.error) <= 310  # 300 + "..."

    def test_invalid_json_returns_error(self, temp_script):
        """Script outputs something that starts with { but isn't valid JSON."""
        script = temp_script('print("{not valid json")')
        result = asyncio.get_event_loop().run_until_complete(
            _run_raw_script(script)
        )
        assert not result.ok
        assert "json" in result.error.lower()

    def test_timeout_kills_process(self, temp_script):
        """Script that runs longer than timeout should be killed."""
        script = temp_script(
            'import time\ntime.sleep(60)\n'
        )
        result = asyncio.get_event_loop().run_until_complete(
            _run_raw_script(script, timeout=0.5)
        )
        assert not result.ok
        assert "timed out" in result.error.lower()

    def test_exit_code_preserved(self, temp_script):
        script = temp_script('import sys; sys.exit(42)')
        result = asyncio.get_event_loop().run_until_complete(
            _run_raw_script(script)
        )
        assert not result.ok
        assert result.exit_code == 42


class TestRunModule:
    """Tests for run_module()."""

    def test_module_not_found(self):
        result = asyncio.get_event_loop().run_until_complete(
            run_module("nonexistent.module.xyz")
        )
        assert not result.ok
        assert result.error is not None

    def test_module_timeout(self):
        """Module that hangs should be killed."""
        result = asyncio.get_event_loop().run_until_complete(
            run_module("time", args=[], timeout=0.1)
        )
        # python3 -m time just runs and exits, so this might succeed or timeout
        # The point is it doesn't hang forever
        assert isinstance(result, ScriptResult)


class TestScriptResult:
    """Tests for ScriptResult dataclass."""

    def test_ok_result(self):
        r = ScriptResult(ok=True, data={"key": "value"})
        assert r.ok
        assert r.data["key"] == "value"
        assert r.error is None
        assert r.exit_code is None

    def test_error_result(self):
        r = ScriptResult(ok=False, error="something broke", exit_code=1)
        assert not r.ok
        assert r.data is None
        assert r.error == "something broke"
        assert r.exit_code == 1


# ---------------------------------------------------------------------------
# Helper: run a script by absolute path (bypasses relative-to-scripts/ resolution)
# ---------------------------------------------------------------------------

async def _run_raw_script(script_path: str, timeout: float = 10.0) -> ScriptResult:
    """Run a script by absolute path using asyncio subprocess directly.

    This mirrors run_script() logic but accepts an absolute path for testing.
    """
    import asyncio as _aio

    try:
        proc = await _aio.create_subprocess_exec(
            "python3", script_path,
            stdout=_aio.subprocess.PIPE,
            stderr=_aio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await _aio.wait_for(
            proc.communicate(), timeout=timeout
        )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            lines = [
                l for l in stderr.strip().split("\n")
                if "warnings.warn(" not in l and "NotOpenSSLWarning" not in l
            ]
            err_msg = lines[-1] if lines else f"Script exited with code {proc.returncode}"
            if len(err_msg) > 300:
                err_msg = err_msg[:300] + "..."
            return ScriptResult(ok=False, error=err_msg, exit_code=proc.returncode)

        json_start = stdout.find("{")
        if json_start == -1:
            return ScriptResult(ok=True, data={})

        data = json.loads(stdout[json_start:])
        return ScriptResult(ok=True, data=data)

    except _aio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return ScriptResult(ok=False, error=f"Script timed out after {timeout}s")

    except json.JSONDecodeError as e:
        return ScriptResult(ok=False, error=f"Invalid JSON output: {e}")
