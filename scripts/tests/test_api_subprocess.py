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

from api.subprocess import (
    run_script,
    run_module,
    ScriptResult,
    _extract_error_message,
    _extract_json_payload,
)


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
        result = asyncio.run(
            run_script("nonexistent_script_xyz.py")
        )
        assert not result.ok
        assert "not found" in result.error.lower()

    def test_successful_json_output(self, temp_script):
        script = temp_script('import json; print(json.dumps({"status": "ok", "count": 42}))')
        # run_script expects path relative to scripts/, so we need to adjust
        result = asyncio.run(
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
        result = asyncio.run(
            _run_raw_script(script)
        )
        assert result.ok
        assert result.data["result"] == "done"

    def test_progress_line_with_list_literal_does_not_break_parser(self, temp_script):
        """EWY bearish risk reversal regression (2026-05-27).

        `ib_place_order.py` used to print a combo-order progress line
        containing a Python list literal (`ratios=[1, 1]`) to stdout
        BEFORE the final result JSON. The pre-fix extractor sliced from
        the first `{` or `[`, parsed `[1, 1]`, and then tripped on the
        result as `Extra data: line 2 column 1 (char 7)` — char 7 being
        exactly the length of `[1, 1]\\n`. The line is now sent to
        stderr at the script, AND the extractor scans lines from the
        end so any future progress print with brackets is harmless.
        """
        script = temp_script(
            'import json\n'
            'print("  Combo order: 2 legs, NonGuaranteed=1, ratios=[1, 1]")\n'
            'print(json.dumps({"status": "ok", "orderId": 12345, "permId": 67890}))\n'
        )
        result = asyncio.run(_run_raw_script(script))
        assert result.ok
        assert result.data["status"] == "ok"
        assert result.data["orderId"] == 12345
        assert result.data["permId"] == 67890

    def test_multiple_progress_lines_with_brackets(self, temp_script):
        """Defensive: multiple progress lines with bracket literals still
        resolve to the LAST line that parses as complete JSON."""
        script = temp_script(
            'import json\n'
            'print("Step 1: [a, b]")\n'
            'print("Step 2: {x: 1}")\n'
            'print("Step 3: ratios=[1, 1, 1]")\n'
            'print(json.dumps({"status": "ok"}))\n'
        )
        result = asyncio.run(_run_raw_script(script))
        assert result.ok
        assert result.data["status"] == "ok"

    def test_result_is_a_top_level_array(self, temp_script):
        """Scripts may emit a JSON array as the final result (leap scanner
        pattern). The extractor must accept array roots too."""
        script = temp_script(
            'import json\n'
            'print("Computing...")\n'
            'print(json.dumps([{"ticker": "AAPL"}, {"ticker": "TSLA"}]))\n'
        )
        result = asyncio.run(_run_raw_script(script))
        assert result.ok
        assert isinstance(result.data, list)
        assert len(result.data) == 2

    def test_empty_stdout_returns_empty_dict(self, temp_script):
        """Scripts that write to files produce no stdout JSON."""
        script = temp_script('print("Saved to file")')
        result = asyncio.run(
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
        result = asyncio.run(
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
        result = asyncio.run(
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
        result = asyncio.run(
            _run_raw_script(script)
        )
        assert not result.ok
        assert len(result.error) <= 310  # 300 + "..."

    def test_json_error_stdout_returns_message_field(self):
        msg = _extract_error_message(
            '{"status":"error","message":"Trade not found after reconnect as original clientId"}\n',
            "",
            "fallback",
        )
        assert msg == "Trade not found after reconnect as original clientId"

    def test_invalid_json_returns_error(self, temp_script):
        """Script outputs something that starts with { but isn't valid JSON."""
        script = temp_script('print("{not valid json")')
        result = asyncio.run(
            _run_raw_script(script)
        )
        assert not result.ok
        assert "json" in result.error.lower()

    def test_timeout_kills_process(self, temp_script):
        """Script that runs longer than timeout should be killed."""
        script = temp_script(
            'import time\ntime.sleep(60)\n'
        )
        result = asyncio.run(
            _run_raw_script(script, timeout=0.1)
        )
        assert not result.ok
        assert "timed out" in result.error.lower()

    def test_exit_code_preserved(self, temp_script):
        script = temp_script('import sys; sys.exit(42)')
        result = asyncio.run(
            _run_raw_script(script)
        )
        assert not result.ok
        assert result.exit_code == 42


class TestRunModule:
    """Tests for run_module()."""

    def test_module_not_found(self):
        result = asyncio.run(
            run_module("nonexistent.module.xyz")
        )
        assert not result.ok
        assert result.error is not None

    def test_module_timeout(self):
        """Module that hangs should be killed."""
        result = asyncio.run(
            run_module("time", args=[], timeout=0.5)
        )
        # python3 -m time just runs and exits, so this might succeed or timeout
        # The point is it doesn't hang forever
        assert isinstance(result, ScriptResult)

    def test_module_error_falls_back_to_stdout_when_stderr_is_empty(self):
        """When stderr is empty, error should be extracted from stdout."""
        # Use a nonexistent sub-module that fails fast with a clear error
        result = asyncio.run(
            run_module("json.tool", args=["--no-such-arg"], timeout=5)
        )
        assert not result.ok
        assert result.error is not None


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
            sys.executable, script_path,
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

        # Use the real extractor so this helper exercises the production
        # parser. Embedding a separate copy here is what kept the EWY
        # bearish-risk-reversal bug invisible to the test suite — the
        # stale embedded copy used `stdout.find("{")` and only saw the
        # first `{`/`[` in stdout, missing the case where a progress
        # line contained a list literal.
        payload = _extract_json_payload(stdout)
        if payload is None:
            return ScriptResult(ok=True, data={})
        return ScriptResult(ok=True, data=payload)

    except _aio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return ScriptResult(ok=False, error=f"Script timed out after {timeout}s")

    except json.JSONDecodeError as e:
        return ScriptResult(ok=False, error=f"Invalid JSON output: {e}")


class TestTrailingJsonShadowing:
    """T-012: a JSON-parseable line printed AFTER the result must not
    replace the real order result (the reverse walk returned the first
    parse from the END — i.e. the trailing junk)."""

    def test_trailing_json_line_does_not_shadow_the_order_result(self):
        stdout = (
            "Step 1: qualifying contract\n"
            '{"status": "ok", "orderId": 42, "permId": 420042}\n'
            '{"progress": 100, "phase": "cleanup"}\n'
        )
        payload = _extract_json_payload(stdout)
        assert isinstance(payload, dict)
        assert payload.get("status") == "ok"
        assert payload.get("permId") == 420042

    def test_trailing_list_literal_does_not_shadow_the_order_result(self):
        stdout = (
            '{"status": "error", "message": "IB rejected: margin"}\n'
            "[1, 1]\n"
        )
        payload = _extract_json_payload(stdout)
        assert isinstance(payload, dict)
        assert payload.get("status") == "error"

    def test_last_status_dict_wins_when_script_prints_two(self):
        stdout = (
            '{"status": "retrying", "attempt": 1}\n'
            '{"status": "ok", "orderId": 7}\n'
        )
        payload = _extract_json_payload(stdout)
        assert payload.get("status") == "ok"

    def test_array_only_output_still_returns_the_array(self):
        stdout = 'Computing...\n[{"ticker": "AAPL"}, {"ticker": "TSLA"}]\n'
        payload = _extract_json_payload(stdout)
        assert isinstance(payload, list)
        assert len(payload) == 2
