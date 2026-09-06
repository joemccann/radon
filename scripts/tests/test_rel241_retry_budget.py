"""REL-241 (R-646): the portfolio-refresh retry budget must fit inside the
systemd unit's TimeoutStartSec with margin.

Worst case is a sustained 502: every curl runs to its -m timeout and every
retry sleeps the full delay. If that wall time meets or exceeds
TimeoutStartSec, systemd kills the oneshot as Result=timeout — a failed unit
and a watchdog page for the exact condition the retries absorb.

T-474: the previous test regex-scraped constants and re-modelled the loop
shape (`attempts = retries + 1`) in Python, so a changed loop bound with
unchanged constants still passed. This version EXECUTES the real script with
PATH-shimmed `curl`/`sleep` stubs that log every invocation and force a
sustained 502, then measures the curl count and simulated wall clock from
the logs. A loop-bound change now changes the measurement and re-fails.
"""

import os
import re
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "run_portfolio_refresh.sh"
UNIT = REPO_ROOT / "cloud" / "services" / "radon-portfolio-sync.service"

# Headroom for the wrapper's non-curl work (python probes, logging, exec).
MARGIN_SECS = 10


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _timeout_start_sec() -> int:
    match = re.search(r"^TimeoutStartSec=(\d+)$", UNIT.read_text(), re.M)
    assert match, f"TimeoutStartSec not found in {UNIT}"
    return int(match.group(1))


def _documented_retries() -> int:
    """The script's shipped retry default — used only to cross-check the
    MEASURED curl count against the documented budget, never to model the
    loop shape."""
    code = "\n".join(
        line
        for line in SCRIPT.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
    return int(re.search(r"RADON_PORTFOLIO_REFRESH_RETRIES:-(\d+)", code).group(1))


def _run_script_under_sustained_502(tmp_path: Path) -> tuple[int, list[int], list[int]]:
    """Execute the real wrapper with curl/sleep stubs. Returns (exit code,
    per-curl -m timeouts, per-sleep delays), each list one entry per real
    invocation the script made."""
    shim = tmp_path / "bin"
    shim.mkdir()
    curl_log = tmp_path / "curl.log"
    sleep_log = tmp_path / "sleep.log"

    # Trading-gate python: pass resolve_python's `-c` probe, answer the
    # IS_TRADING heredoc with "yes" so the retry loop is reached.
    _write_executable(
        shim / "python-stub",
        '#!/bin/bash\nif [ "${1:-}" = "-c" ]; then exit 0; fi\ncat >/dev/null\necho yes\n',
    )
    # curl: log the -m timeout it was given, emit the -w http_code as a
    # sustained 502, exit 22 (curl -f on a 5xx) — retryable per the script.
    _write_executable(
        shim / "curl",
        "#!/bin/bash\n"
        'm=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "-m" ]; then m="$2"; shift; fi\n'
        "  shift\n"
        "done\n"
        f'echo "$m" >> "{curl_log}"\n'
        "printf '502'\n"
        "exit 22\n",
    )
    # sleep: log the requested delay, return instantly.
    _write_executable(
        shim / "sleep",
        f'#!/bin/bash\necho "$1" >> "{sleep_log}"\nexit 0\n',
    )

    env = {
        **os.environ,
        "PATH": f"{shim}:{os.environ['PATH']}",
        "RADON_PYTHON_BIN": str(shim / "python-stub"),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    curls = [int(x) for x in curl_log.read_text().split()] if curl_log.exists() else []
    sleeps = (
        [int(x) for x in sleep_log.read_text().split()] if sleep_log.exists() else []
    )
    return result.returncode, curls, sleeps


def test_measured_retry_wall_time_fits_timeout_start_sec_with_margin(tmp_path):
    returncode, curls, sleeps = _run_script_under_sustained_502(tmp_path)

    # The budget must be exhausted and the unit must fail (pages as designed).
    assert returncode == 22, (returncode, curls, sleeps)
    assert curls, "the script never invoked curl"

    # Measured invocation count matches the documented budget: one initial
    # attempt plus the shipped retry default, and one sleep per retry.
    retries = _documented_retries()
    assert len(curls) == retries + 1, (
        f"measured {len(curls)} curl invocations under sustained 502; "
        f"documented budget is {retries} retries + 1 initial attempt"
    )
    assert len(sleeps) == retries, (
        f"measured {len(sleeps)} sleeps; expected one per retry ({retries})"
    )

    # Worst-case simulated wall clock: every curl runs to its own -m timeout
    # and every sleep runs its full delay — as invoked, not as modelled.
    worst_case = sum(curls) + sum(sleeps)
    timeout = _timeout_start_sec()
    assert worst_case + MARGIN_SECS <= timeout, (
        f"measured worst-case wall time {worst_case}s "
        f"({len(curls)} curls at -m {curls} + sleeps {sleeps}) "
        f"+ {MARGIN_SECS}s margin exceeds TimeoutStartSec={timeout}"
    )
