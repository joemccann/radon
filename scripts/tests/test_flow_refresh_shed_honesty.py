"""flow-refresh must not report success for a run that did nothing.

R-221: `_retryable_flow_shed` classified a shed purely by HTTP status, and
curl threw the body away (`-o /dev/null`). But `_run_flow_tab` raises
`HTTPException(502, detail=result.error)` for EVERY `result.ok is False` —
capacity exhaustion, a nonzero script exit, an asyncio timeout at the script
deadline, invalid JSON, any other exception. So a `scanner.py` traceback or a
UW outage inside `discover.py` was diagnosed as "shed for subprocess capacity",
retried twice (re-launching the doomed script and re-burning its UW spend), and
remapped to exit 0. systemd recorded success, the operator saw green, and the
three caches quietly stopped advancing with no `service_health` row anywhere.

R-222: the curl-exit-7 fallback ran the python scan with no timeout, no
`timeout(1)` wrapper, and entirely outside `SCAN_DEADLINE` — which is only
consulted inside the retry loop. With FastAPI down, connection-refused is
instant, so all three scans reached an unbounded fallback and systemd
SIGTERMed the cgroup mid-run.

R-223: `IS_TRADING` collapsed to the string "no" through TWO independent
swallows, so a broken venv or a renamed `utils.market_calendar` printed
"Market closed - skipping flow refresh" and exited 0 — indistinguishable from
a normal weekend skip, on every hourly fire.
"""

from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

# The wrapper harness lives beside this file; pytest's rootdir is the repo, so
# the directory is not on sys.path by default.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_run_flow_refresh_wrapper import (  # noqa: E402
    _FastApiStub,
    _executable,
    _free_port,
    _repo,
    _run,
    _stage_python,
)

WRAPPER = Path(__file__).resolve().parents[1] / "run_flow_refresh.sh"
SCAN_PATH = "/scan"


def _stage_broken_calendar_python(bin_dir: Path) -> Path:
    """A python whose market-state probe fails, rather than answering 'no'."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    py = bin_dir / "python3.13"
    _executable(
        py,
        textwrap.dedent(
            """\
            #!/bin/bash
            if [ "$1" = "-" ]; then
                cat >/dev/null
                echo "ModuleNotFoundError: utils.market_calendar" >&2
                exit 1
            fi
            if [ "$1" = "-c" ]; then
                exit 0
            fi
            exec /usr/bin/env python3 "$@"
            """
        ),
    )
    return py


class TestMarketStateIsDeterminable:
    def test_an_unusable_probe_is_not_reported_as_market_closed(self, tmp_path):
        repo = _repo(tmp_path)
        python_bin = _stage_broken_calendar_python(tmp_path / "bin")
        result = _run(repo, python_bin, _free_port())

        combined = result.stdout + result.stderr
        assert "Market closed" not in combined, (
            "a broken market-state probe read as a normal weekend skip"
        )
        assert result.returncode != 0, (
            "an indeterminate market state exited 0, so systemd saw a clean run"
        )

    def test_a_genuine_closure_still_exits_clean(self, tmp_path):
        repo = _repo(tmp_path)
        python_bin = _stage_python(tmp_path / "bin", trading_day=False)
        result = _run(repo, python_bin, _free_port())
        assert result.returncode == 0
        assert "Market closed" in result.stdout


class TestShedClassification:
    def test_a_script_failure_is_not_diagnosed_as_a_capacity_shed(self, tmp_path):
        """The API answers 502 for every failure, so the BODY is the signal."""
        repo = _repo(tmp_path)
        python_bin = _stage_python(tmp_path / "bin", trading_day=True)
        port = _free_port()
        stub = _FastApiStub(
            port,
            fail_paths=frozenset({SCAN_PATH}),
            fail_status=502,
            fail_body=b'{"detail": "scanner.py exited 1: Traceback ..."}',
        )
        stub.start()
        try:
            # delay=1: with delay=0 the wrapper correctly reports "deadline
            # reached before retry", which is a different branch.
            result = _run(repo, python_bin, port, retries=2, delay=1)
        finally:
            stub.stop()

        assert stub.hits.get(SCAN_PATH, 0) == 1, (
            f"a doomed script was relaunched {stub.hits.get(SCAN_PATH)}x, "
            "re-burning its UW spend"
        )
        assert result.returncode != 0, "a script traceback exited 0"

    def test_a_real_capacity_shed_still_retries_and_sheds(self, tmp_path):
        repo = _repo(tmp_path)
        python_bin = _stage_python(tmp_path / "bin", trading_day=True)
        port = _free_port()
        stub = _FastApiStub(
            port,
            fail_paths=frozenset({SCAN_PATH}),
            fail_status=502,
            fail_body=b'{"detail": "Subprocess capacity exhausted (3 active, lane cap 3)"}',
        )
        stub.start()
        try:
            # delay=1: with delay=0 the wrapper correctly reports "deadline
            # reached before retry", which is a different branch.
            result = _run(repo, python_bin, port, retries=2, delay=1)
        finally:
            stub.stop()

        assert stub.hits.get(SCAN_PATH, 0) == 3, (
            f"a genuine capacity shed was not retried: {stub.hits}"
        )
        assert "capacity" in (result.stdout + result.stderr).lower()


class TestFallbackIsBounded:
    def test_the_direct_fallback_runs_under_a_deadline(self):
        source = WRAPPER.read_text(encoding="utf-8")
        fallback = re.search(
            r'FastAPI unavailable, fallback to.*?\n(.*?)\n\s*echo "\$\(date\): \$\{label\} refresh FAILED"',
            source,
            re.S,
        )
        assert fallback, "the fallback branch moved; update this assertion"
        body = fallback.group(1)
        assert "timeout" in body, (
            "the curl-exit-7 fallback runs the python scan unbounded and "
            "outside SCAN_DEADLINE, so systemd SIGTERMs the cgroup mid-run"
        )

    def test_the_stated_budget_holds_for_three_scans(self):
        source = WRAPPER.read_text(encoding="utf-8")
        scan_timeout = int(
            re.search(r'SCAN_TIMEOUT="\$\{RADON_FLOW_REFRESH_SCAN_TIMEOUT:-(\d+)\}"', source).group(1)
        )
        unit = (
            Path(__file__).resolve().parents[2]
            / "cloud" / "services" / "radon-flow-refresh.service"
        ).read_text(encoding="utf-8")
        start_limit = int(re.search(r"TimeoutStartSec=(\d+)", unit).group(1))
        assert 3 * scan_timeout <= start_limit, (
            f"three {scan_timeout}s scans do not fit inside TimeoutStartSec="
            f"{start_limit}, which is what the wrapper's comment claims"
        )


class TestPermanentShedIsVisible:
    def test_the_unit_does_not_report_exit_75_as_success(self):
        raw = (
            Path(__file__).resolve().parents[2]
            / "cloud" / "services" / "radon-flow-refresh.service"
        ).read_text(encoding="utf-8")
        # Strip comments first: the directive's absence is documented in a
        # comment that quotes it, and a naive substring check matches that.
        unit = "\n".join(
            line for line in raw.splitlines() if not line.lstrip().startswith("#")
        )
        assert "SuccessExitStatus=75" not in unit, (
            "a permanently shedding unit — itself the incident — is "
            "indistinguishable from a clean run in systemctl is-failed"
        )

    def test_a_shed_writes_a_service_health_row(self):
        source = WRAPPER.read_text(encoding="utf-8")
        assert "service_health" in source or "record_service_health" in source, (
            "nothing on the shed path writes a health row, so the watchdog "
            "has no error row either"
        )
