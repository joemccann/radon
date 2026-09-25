"""Stopping an app unit must not depend on `podman run --sig-proxy`.

2026-09-23..25: six deploys rolled back on `timed out waiting for
radon-<unit>.service to become inactive` (nextjs, monitor, newsfeed). In each
hung stop systemd signalled the foreground `podman run` client, but the app
inside the container never logged its SIGTERM handler and kept working (the
newsfeed ran a scrape cycle 5s after "Stopping"); it ran until systemd's 90s
SIGKILL, past the deploy helper's 60s inactive wait. In the fast stops of the
same deploys the app logged SIGTERM within a second.

The fix: an explicit ExecStop that asks the engine to stop the container by
name (`podman stop --time <grace>`: SIGTERM to the container init, SIGKILL
after the grace), bounded so the whole stop fits inside both the unit's
TimeoutStopSec and the deploy helper's inactive wait.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_app_runtime import APP_UNITS, _run  # noqa: E402

CLOUD = Path(__file__).resolve().parents[1]
SERVICES = CLOUD / "services"
HELPER = CLOUD / "scripts" / "deploy-root-helper.sh"

SYSTEMD_DEFAULT_STOP_TIMEOUT = 90
# `podman stop` returns once the container is gone; allow for its own
# bookkeeping plus ExecStopPost's `rm -f` after the grace-period SIGKILL.
STOP_OVERHEAD_SECONDS = 5
# The monitor saves state and finishes fill/journal handlers on SIGTERM.
MONITOR_MIN_GRACE_SECONDS = 20

HALT = re.compile(r"^ExecStop=/usr/local/sbin/radon-app-runtime halt %n (\d+)$", re.M)


def _drop_in(unit: str) -> str:
    return (SERVICES / f"{unit}.d" / "runtime-container.conf").read_text(encoding="utf-8")


def _grace(unit: str) -> int:
    match = HALT.search(_drop_in(unit))
    assert match, f"{unit} has no ExecStop that stops its container through the engine"
    return int(match.group(1))


def _unit_stop_timeout(unit: str) -> int:
    text = (SERVICES / unit).read_text(encoding="utf-8") + "\n" + _drop_in(unit)
    values = re.findall(r"^TimeoutStopSec=(\d+)$", text, re.M)
    return int(values[-1]) if values else SYSTEMD_DEFAULT_STOP_TIMEOUT


def _helper_stop_wait(unit: str) -> int:
    helper = HELPER.read_text(encoding="utf-8")
    name = "RESEARCH_STOP_WAIT_SECONDS" if unit == "radon-research.service" else "STATE_WAIT_SECONDS"
    return int(re.search(rf"^  readonly {name}=(\d+)$", helper, re.M).group(1))


@pytest.mark.parametrize("unit", APP_UNITS)
def test_every_drop_in_stops_its_container_through_the_engine(unit: str) -> None:
    text = _drop_in(unit)
    assert len(HALT.findall(text)) == 1
    # ExecStop runs before systemd signals the podman client, so the engine
    # stop is what delivers SIGTERM; ExecStopPost still reaps.
    assert text.index("ExecStop=") < text.index("ExecStopPost=")


@pytest.mark.parametrize("unit", APP_UNITS)
def test_stop_grace_fits_the_unit_timeout_and_the_deploy_wait(unit: str) -> None:
    budget = _grace(unit) + STOP_OVERHEAD_SECONDS
    assert budget <= _unit_stop_timeout(unit), (
        f"{unit}: systemd would cut the engine stop short and fall back to the "
        "signal-proxy path this ExecStop replaces"
    )
    assert budget <= _helper_stop_wait(unit), (
        f"{unit}: a container that ignores SIGTERM would still outlast the "
        "deploy helper's inactive wait and roll the deploy back"
    )


def test_monitor_keeps_a_real_sigterm_window() -> None:
    assert _grace("radon-monitor.service") >= MONITOR_MIN_GRACE_SECONDS


def _engine_stub(present: bool, stop_ok: bool = True) -> str:
    # `container inspect` answers presence; `stop` succeeds or fails.
    return (
        "#!/bin/bash\n"
        "printf '%s\\n' \"$*\" >> {log}\n"
        'if [[ "$1 $2" == "container inspect" ]]; then exit '
        + ("0" if present else "1")
        + "; fi\n"
        + ('if [[ "$1" == stop ]]; then exit 1; fi\n' if not stop_ok else "")
        + "exit 0\n"
    )


@pytest.mark.parametrize("unit", APP_UNITS)
def test_halt_stops_the_named_container_with_the_given_grace(tmp_path: Path, unit: str) -> None:
    result = _run(tmp_path, ["halt", unit, "30"], docker_body=_engine_stub(present=True))
    assert result.returncode == 0, result.stderr
    assert f"stop --time 30 {unit}" in result.docker_log.read_text().splitlines()


def test_halt_is_a_no_op_when_the_container_already_exited(tmp_path: Path) -> None:
    # systemd also runs ExecStop after the main process exits on its own.
    result = _run(tmp_path, ["halt", "radon-monitor.service", "30"], docker_body=_engine_stub(present=False))
    assert result.returncode == 0, result.stderr
    assert not [l for l in result.docker_log.read_text().splitlines() if l.startswith("stop ")]


def test_halt_fails_loudly_when_the_container_survives_a_failed_stop(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        ["halt", "radon-newsfeed.service", "30"],
        docker_body=_engine_stub(present=True, stop_ok=False),
    )
    assert result.returncode == 75
    assert "radon-newsfeed.service" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["halt", "radon-ib-gateway.service", "30"],
        ["halt", "radon-watchdog.service", "30"],
        ["halt", "radon-api.service"],
        ["halt", "radon-api.service", "soon"],
    ],
)
def test_halt_refuses_other_units_and_malformed_graces(tmp_path: Path, args: list[str]) -> None:
    result = _run(tmp_path, args, docker_body=_engine_stub(present=True))
    assert result.returncode == 64
    log = result.docker_log.read_text() if result.docker_log.exists() else ""
    assert not [l for l in log.splitlines() if l.startswith("stop ")]
