"""REL-241 (R-646): the portfolio-refresh retry budget must fit inside the
systemd unit's TimeoutStartSec with margin.

Worst case is a sustained 502: every curl runs to its -m timeout and every
retry sleeps the full delay. If that wall time meets or exceeds
TimeoutStartSec, systemd kills the oneshot as Result=timeout — a failed unit
and a watchdog page for the exact condition the retries absorb. Constants
are parsed from the script and unit file so drift in either re-fails this.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "run_portfolio_refresh.sh"
UNIT = REPO_ROOT / "cloud" / "services" / "radon-portfolio-sync.service"

# Headroom for the wrapper's non-curl work (python probes, logging, exec).
MARGIN_SECS = 10


def _script_code_lines() -> list[str]:
    # Strip comment lines first: the retry comment block quotes code-like
    # text (502/503, exit 7) and must not feed the constant parse.
    return [
        line
        for line in SCRIPT.read_text().splitlines()
        if not line.lstrip().startswith("#")
    ]


def _script_constants() -> tuple[int, int, int]:
    code = "\n".join(_script_code_lines())
    curl_timeout = int(re.search(r"curl\s[^\n]*-m\s+(\d+)", code).group(1))
    retries = int(
        re.search(r"RADON_PORTFOLIO_REFRESH_RETRIES:-(\d+)", code).group(1)
    )
    delay = int(
        re.search(
            r"RADON_PORTFOLIO_REFRESH_RETRY_DELAY_SECS:-(\d+)", code
        ).group(1)
    )
    return curl_timeout, retries, delay


def _timeout_start_sec() -> int:
    match = re.search(r"^TimeoutStartSec=(\d+)$", UNIT.read_text(), re.M)
    return int(match.group(1))


def test_worst_case_retry_wall_time_fits_timeout_start_sec_with_margin():
    curl_timeout, retries, delay = _script_constants()
    attempts = retries + 1
    worst_case = attempts * curl_timeout + retries * delay
    timeout = _timeout_start_sec()
    assert worst_case + MARGIN_SECS <= timeout, (
        f"worst-case retry wall time {worst_case}s "
        f"({attempts} curls x -m {curl_timeout} + {retries} sleeps x {delay}s) "
        f"+ {MARGIN_SECS}s margin exceeds TimeoutStartSec={timeout}"
    )
