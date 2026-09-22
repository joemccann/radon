"""Every nightly/weekend wrapper must resolve a usable GNU timeout.

An empty TIMEOUT_BIN turns `"$TIMEOUT_BIN" <secs> cmd` into an exec of ""
under set -u/-e, so every net_bounded call and phase launch hard-fails on a
host where coreutils installs the binary as gtimeout (macOS). The wrapper
must fall back to gtimeout and fail closed with a named reason when neither
exists. The hard require stays after --lock-lib-only so setup_* can source
pid_alive/acquire/sweep on hosts without GNU timeout.
"""

from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent

WRAPPERS = [
    "ci_performance_nightly.sh",
    "documentation_nightly.sh",
    "reliability_weekend.sh",
    "security_nightly.sh",
    "security_deepsec_nightly.sh",
    "testing_weekend.sh",
]


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_timeout_bin_falls_back_to_gtimeout(wrapper: str) -> None:
    text = (SCRIPTS / wrapper).read_text(encoding="utf-8")
    assert (
        'TIMEOUT_BIN="$(command -v timeout || command -v gtimeout || true)"' in text
    ), wrapper


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_missing_timeout_fails_closed_after_lib_only_return(wrapper: str) -> None:
    text = (SCRIPTS / wrapper).read_text(encoding="utf-8")
    guard = text.find('[[ -n "$TIMEOUT_BIN" ]] ||')
    assert guard != -1, wrapper
    lib_only = text.find('"${1:-}" == "--lock-lib-only"')
    assert lib_only != -1, wrapper
    assert lib_only < guard, wrapper
