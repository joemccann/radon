from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_DIR / "config"
SCRIPTS_DIR = PROJECT_DIR / "scripts"


def _load_plist(name: str) -> dict:
    with (CONFIG_DIR / name).open("rb") as handle:
        return plistlib.load(handle)


@pytest.mark.parametrize(
    "name",
    ["com.radon.monitor-daemon.plist", "com.radon.exit-order-service.plist"],
)
def test_python_launch_agents_use_python313_from_explicit_path(name: str) -> None:
    plist = _load_plist(name)
    assert plist["ProgramArguments"][:2] == ["/usr/bin/env", "python3.13"]

    path = plist["EnvironmentVariables"]["PATH"].split(":")
    assert path[:2] == ["/opt/homebrew/bin", "/usr/local/bin"]
    assert "/usr/bin" in path


def test_monitor_launch_agent_does_not_amplify_scheduled_failures() -> None:
    plist = _load_plist("com.radon.monitor-daemon.plist")

    assert plist["ProgramArguments"][-1] == "--once"
    assert plist["StartInterval"] == 60
    assert plist.get("KeepAlive") is not True
    assert not isinstance(plist.get("KeepAlive"), dict)


def test_data_refresh_plist_has_explicit_launchd_path() -> None:
    plist = _load_plist("com.radon.data-refresh.plist")
    path = plist["EnvironmentVariables"]["PATH"].split(":")
    assert path[:2] == ["/opt/homebrew/bin", "/usr/local/bin"]
    assert "/usr/bin" in path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _monitor_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "radon"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS_DIR / "setup_monitor_daemon.sh", scripts)

    home = tmp_path / "home"
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.radon.monitor-daemon.plist").write_text("installed")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    return project, home, fake_bin


def _run_monitor_status(
    tmp_path: Path,
    *,
    launchctl_output: str,
    state_age_seconds: int,
) -> subprocess.CompletedProcess[str]:
    project, home, fake_bin = _monitor_project(tmp_path)
    fixture = tmp_path / "launchctl.txt"
    fixture.write_text(launchctl_output)
    _write_executable(
        fake_bin / "launchctl",
        "#!/bin/sh\n"
        'test "$1" = "print" || exit 64\n'
        'cat "$LAUNCHCTL_FIXTURE"\n',
    )

    state_file = project / "data" / "daemon_state.json"
    state_file.parent.mkdir()
    state_file.write_text(json.dumps({"saved_at": "fixture", "handlers": {}}))
    timestamp = time.time() - state_age_seconds
    os.utime(state_file, (timestamp, timestamp))

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "LAUNCHCTL_FIXTURE": str(fixture),
            "MONITOR_STATE_STALE_SECONDS": "300",
        }
    )
    return subprocess.run(
        ["/bin/bash", str(project / "scripts" / "setup_monitor_daemon.sh"), "status"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_monitor_status_reports_scheduled_idle_after_success(tmp_path: Path) -> None:
    result = _run_monitor_status(
        tmp_path,
        launchctl_output="""
gui/501/com.radon.monitor-daemon = {
    state = not running
    runs = 42
    last exit code = 0
}
""",
        state_age_seconds=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Service: LOADED (scheduled, idle)" in result.stdout
    assert "Service: RUNNING" not in result.stdout


def test_monitor_status_treats_first_xpcproxy_start_as_pending(tmp_path: Path) -> None:
    result = _run_monitor_status(
        tmp_path,
        launchctl_output="""
gui/501/com.radon.monitor-daemon = {
    state = xpcproxy
    runs = 1
}
""",
        state_age_seconds=900,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Service: ACTIVE (first scheduled run is starting)" in result.stdout
    assert "State heartbeat: PENDING" in result.stdout


def test_monitor_status_fails_on_latest_nonzero_exit(tmp_path: Path) -> None:
    result = _run_monitor_status(
        tmp_path,
        launchctl_output="""
gui/501/com.radon.monitor-daemon = {
    state = spawn scheduled
    runs = 7250
    last exit code = 1
}
""",
        state_age_seconds=10,
    )

    assert result.returncode != 0
    assert "Service: FAILED (last exit code: 1)" in result.stdout


def test_monitor_status_fails_on_stale_state_heartbeat(tmp_path: Path) -> None:
    result = _run_monitor_status(
        tmp_path,
        launchctl_output="""
gui/501/com.radon.monitor-daemon = {
    state = not running
    runs = 42
    last exit code = 0
}
""",
        state_age_seconds=900,
    )

    assert result.returncode != 0
    assert "State heartbeat: STALE" in result.stdout


def test_monitor_install_preflight_checks_python_and_dependencies(tmp_path: Path) -> None:
    project, home, fake_bin = _monitor_project(tmp_path)
    marker = tmp_path / "probe-ok"
    _write_executable(
        fake_bin / "python3.13",
        "#!/bin/sh\n"
        "probe=$(cat)\n"
        'printf "%s" "$probe" | grep -q "sys.version_info" || exit 21\n'
        'printf "%s" "$probe" | grep -q "ib_insync" || exit 22\n'
        'printf "%s" "$probe" | grep -q "libsql_experimental" || exit 23\n'
        'printf "%s" "$probe" | grep -q "playwright" || exit 24\n'
        'printf "%s" "$probe" | grep -q "pytz" || exit 25\n'
        'touch "$PREFLIGHT_MARKER"\n',
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "RADON_LAUNCHD_PATH": str(fake_bin),
            "PREFLIGHT_MARKER": str(marker),
        }
    )

    result = subprocess.run(
        ["/bin/bash", str(project / "scripts" / "setup_monitor_daemon.sh"), "preflight"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists()
    assert "Python preflight: OK" in result.stdout


def test_monitor_install_stops_before_launchctl_when_preflight_fails(tmp_path: Path) -> None:
    project, home, fake_bin = _monitor_project(tmp_path)
    launchctl_marker = tmp_path / "launchctl-called"
    _write_executable(fake_bin / "python3.13", "#!/bin/sh\ncat >/dev/null\nexit 1\n")
    _write_executable(
        fake_bin / "launchctl",
        f"#!/bin/sh\ntouch '{launchctl_marker}'\n",
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "RADON_LAUNCHD_PATH": str(fake_bin),
        }
    )

    result = subprocess.run(
        ["/bin/bash", str(project / "scripts" / "setup_monitor_daemon.sh"), "install"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "Python 3.13 dependency preflight failed" in result.stdout
    assert not launchctl_marker.exists()


def _refresh_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "radon"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS_DIR / "run_data_refresh.sh", scripts)
    # The wrapper sources interpreter resolution from scripts/lib/python_bin.sh.
    (scripts / "lib").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCRIPTS_DIR / "lib" / "python_bin.sh", scripts / "lib" / "python_bin.sh")

    data = project / "data"
    data.mkdir()
    for filename in ("scanner.json", "flow_analysis.json", "discover.json"):
        (data / filename).write_text('{"source":"old"}\n')

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "date",
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *+%H*) printf "%s\\n" "${FAKE_ET_HOUR:-15}" ;;\n'
        '  *+%Y-%m-%d*) echo 2026-07-10 ;;\n'
        '  *) echo fixture-date ;;\n'
        "esac\n",
    )

    fake_python = tmp_path / "python3.13"
    _write_executable(
        fake_python,
        "#!/bin/bash\n"
        'if [[ "$1" == "-" && "$#" -eq 1 ]]; then\n'
        "  cat >/dev/null\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "$1" == "-c" ]]; then\n'
        "  echo yes\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "$1" == "-" ]]; then\n'
        "  cat >/dev/null\n"
        "  echo no\n"
        "  exit 0\n"
        "fi\n"
        'printf "%s\\n" "$*" >> "$FAKE_PYTHON_LOG"\n'
        'case "$1" in\n'
        '  *scanner.py) status="${SCANNER_EXIT:-0}" ;;\n'
        '  *flow_analysis.py) status="${FLOW_EXIT:-0}" ;;\n'
        '  *discover.py) status="${DISCOVER_EXIT:-0}" ;;\n'
        '  *cri_scan.py) status="${CRI_EXIT:-0}" ;;\n'
        '  *repair_cri_rvol_cache.py) status="${CRI_REPAIR_EXIT:-0}" ;;\n'
        "  *) status=97 ;;\n"
        "esac\n"
        'echo \'{"source":"new"}\'\n'
        'exit "$status"\n',
    )
    return project, fake_bin, fake_python


def _run_refresh(
    tmp_path: Path,
    *,
    scanner_exit: int = 0,
    flow_exit: int = 0,
    discover_exit: int = 0,
    cri_exit: int = 0,
    cri_repair_exit: int = 0,
    hour_et: int = 15,
) -> tuple[subprocess.CompletedProcess[str], Path, list[str]]:
    project, fake_bin, fake_python = _refresh_project(tmp_path)
    command_log = tmp_path / "python-commands.log"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "RADON_PYTHON_BIN": str(fake_python),
            "FAKE_PYTHON_LOG": str(command_log),
            "SCANNER_EXIT": str(scanner_exit),
            "FLOW_EXIT": str(flow_exit),
            "DISCOVER_EXIT": str(discover_exit),
            "CRI_EXIT": str(cri_exit),
            "CRI_REPAIR_EXIT": str(cri_repair_exit),
            "FAKE_ET_HOUR": str(hour_et),
        }
    )
    result = subprocess.run(
        ["/bin/bash", str(project / "scripts" / "run_data_refresh.sh")],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    commands = command_log.read_text().splitlines()
    return result, project, commands


def test_data_refresh_attempts_all_work_and_returns_aggregate_failure(tmp_path: Path) -> None:
    result, project, commands = _run_refresh(
        tmp_path,
        scanner_exit=7,
        flow_exit=0,
        discover_exit=9,
        hour_et=17,
    )

    assert result.returncode != 0
    assert any("scripts/scanner.py" in command for command in commands)
    assert any("scripts/flow_analysis.py" in command for command in commands)
    assert any("scripts/discover.py" in command for command in commands)
    assert any("scripts/cri_scan.py" in command for command in commands)
    assert any("scripts/repair_cri_rvol_cache.py" in command for command in commands)
    assert (project / "data" / "scanner.json").read_text() == '{"source":"old"}\n'
    assert (project / "data" / "flow_analysis.json").read_text() == '{"source":"new"}\n'
    assert (project / "data" / "discover.json").read_text() == '{"source":"old"}\n'
    assert "scanner: FAIL, flow: OK, discover: FAIL" in result.stdout


def test_data_refresh_returns_success_when_all_required_children_succeed(tmp_path: Path) -> None:
    result, project, commands = _run_refresh(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert len([command for command in commands if "scripts/" in command]) == 3
    for filename in ("scanner.json", "flow_analysis.json", "discover.json"):
        assert (project / "data" / filename).read_text() == '{"source":"new"}\n'


def test_post_close_cri_scan_and_repair_failure_is_aggregate_failure(
    tmp_path: Path,
) -> None:
    result, _, commands = _run_refresh(
        tmp_path,
        hour_et=17,
        cri_exit=5,
        cri_repair_exit=6,
    )

    assert result.returncode != 0
    assert any("scripts/scanner.py" in command for command in commands)
    assert any("scripts/flow_analysis.py" in command for command in commands)
    assert any("scripts/discover.py" in command for command in commands)
    assert any("scripts/cri_scan.py" in command for command in commands)
    assert any("scripts/repair_cri_rvol_cache.py" in command for command in commands)
    assert "CRI cache repair failed (exit 6)" in result.stdout


def test_post_close_cri_repair_recovers_failed_scan(tmp_path: Path) -> None:
    result, _, commands = _run_refresh(
        tmp_path,
        hour_et=17,
        cri_exit=5,
        cri_repair_exit=0,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert any("scripts/cri_scan.py" in command for command in commands)
    assert any("scripts/repair_cri_rvol_cache.py" in command for command in commands)
    assert "CRI cache repair complete (OK)" in result.stdout
    assert "cri: OK" in result.stdout


def test_data_refresh_resolver_requires_python313_and_runtime_dependencies() -> None:
    """Interpreter resolution moved to scripts/lib/python_bin.sh, but
    run_data_refresh.sh must still declare the same two guarantees: a 3.13
    floor and the runtime dependencies it cannot run without.

    The shared candidate list does include older interpreters, so the
    "never run this on 3.9" guarantee is now enforced by the version floor
    rather than by omission. That enforcement is covered behaviourally in
    scripts/tests/test_wrapper_python_resolution.py.
    """
    text = (SCRIPTS_DIR / "run_data_refresh.sh").read_text()

    assert 'RADON_PYTHON_MIN_VERSION="3.13"' in text
    assert "radon_resolve_python ib_insync libsql_experimental" in text


def test_data_refresh_setup_generator_preserves_explicit_path(tmp_path: Path) -> None:
    project = tmp_path / "radon"
    scripts = project / "scripts"
    config = project / "config"
    utils = scripts / "utils"
    utils.mkdir(parents=True)
    config.mkdir()
    shutil.copy2(SCRIPTS_DIR / "setup_data_refresh_service.sh", scripts)
    shutil.copy2(SCRIPTS_DIR / "utils" / "launchd_calendar.py", utils)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "python3.13").symlink_to(sys.executable)
    launchd_path = f"{fake_bin}:/usr/bin:/bin"
    env = os.environ.copy()
    env["RADON_LAUNCHD_PATH"] = launchd_path

    result = subprocess.run(
        ["/bin/bash", str(scripts / "setup_data_refresh_service.sh"), "generate"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    with (config / "com.radon.data-refresh.plist").open("rb") as handle:
        plist = plistlib.load(handle)
    assert plist["EnvironmentVariables"]["PATH"] == launchd_path
