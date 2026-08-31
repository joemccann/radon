"""R-380 / R-393 / R-394 / REL-133: the control-plane refresh cannot strand the app tier.

R-380: `deploy.sh:restart_services` calls `refresh_control_plane || return 1`
BETWEEN `stop_services_for_transition` and `start_services_after_transition`,
and the helper returns 66 when ANY `CONTROL_PLANE_SOURCES` entry is missing from
`$CLOUD_SOURCE`. The five `services/radon-*.service.d/runtime-container.conf`
entries exist only at SHAs at or after `702ae26a`, and the INSTALLED helper is
always the newest one, so a rollback to an earlier release enumerates sources
the restored checkout does not have and returns 66 with all five app units down
and no path back.

R-394: the drop-in content gate lives only in `bootstrap-control-plane.sh`, the
path that almost never runs. `refresh_install_file` — the path that installs
them on EVERY deploy, from the radon-writable `/home/radon/radon/cloud` — has no
`services/*` arm at all.

R-393: `test_root_execution_paths._unit_texts()` iterates `SERVICES_DIR`
non-recursively and filters on `.service`, so the drop-ins that flip five units
to `User=root` are invisible to the guard that exists to reject exactly that.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_refresh_control_plane import (  # noqa: E402
    CONTROL_PLANE_SOURCES,
    DEPLOY,
    ROOT_HELPER,
    Sandbox,
    function_body,
)
import test_root_execution_paths as rootpaths  # noqa: E402

CLOUD_ROOT = pathlib.Path(__file__).resolve().parent.parent
API_DROPIN = "services/radon-api.service.d/runtime-container.conf"
API_UNIT = "services/radon-api.service"


def _strip_comments(text: str) -> str:
    """Never assert structure over a comment that quotes the code it explains."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


# --- A. a missing drop-in must not abort the refresh -------------------------


def test_a_missing_dropin_source_is_skipped_not_fatal(tmp_path) -> None:
    """A rollback to a pre-702ae26a checkout has no drop-ins. It must still deploy."""
    box = Sandbox(tmp_path)
    (box.cloud / API_DROPIN).unlink()
    before = (box.rootfs / "etc/systemd/system/radon-api.service.d/runtime-container.conf").read_bytes()

    result = box.run()

    assert result.returncode == 0, result.stderr
    assert "runtime-container.conf" in result.stderr
    installed = box.rootfs / "etc/systemd/system/radon-api.service.d/runtime-container.conf"
    assert installed.read_bytes() == before


def test_a_missing_unit_source_is_still_fatal(tmp_path) -> None:
    """The skip is scoped to drop-ins. A missing .service is still exit 66."""
    box = Sandbox(tmp_path)
    (box.cloud / API_UNIT).unlink()

    result = box.run()

    assert result.returncode == 66, result.stdout + result.stderr


RESTART_STUBS = (
    "prepare_release_transition",
    "payload_paths_changed",
    "stop_services_for_transition",
    "activate_staged_release",
    "recover_pending_transition",
    "refresh_control_plane",
    "start_services_after_transition",
    "install_release_units",
)


def _restart_services(tmp_path: pathlib.Path, *, refresh_rc: int) -> subprocess.CompletedProcess:
    """Source deploy.sh, stub everything restart_services delegates to, run it.

    Every stub appends its name to `calls`; the only side effect that is not
    a log line is `start_services_after_transition` touching `started`. The
    call sits bare under `set -e`, exactly as `run_deploy_internal` calls it,
    so the script's exit status IS the function's return.
    """
    calls = tmp_path / "calls"
    started = tmp_path / "started"
    stubs = "\n".join(
        f"{name}() {{ printf '%s\\n' {name} >> {calls}; {body}; }}"
        for name, body in (
            ("prepare_release_transition", "return 0"),
            ("payload_paths_changed", "return 0"),
            ("stop_services_for_transition", "return 0"),
            ("activate_staged_release", "return 0"),
            ("recover_pending_transition", "return 0"),
            ("refresh_control_plane", f"return {refresh_rc}"),
            ("start_services_after_transition", f"touch {started}"),
            ("install_release_units", "return 0"),
        )
    )
    shell = f"""
set -euo pipefail
source {DEPLOY}
{stubs}
restart_services deadbeef cafef00d
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        env={**os.environ, "NO_COLOR": "1"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    result.calls = calls.read_text(encoding="utf-8").split() if calls.exists() else []  # type: ignore[attr-defined]
    result.started = started.exists()  # type: ignore[attr-defined]
    return result


def test_restart_services_restarts_the_app_tier_when_the_refresh_fails(tmp_path) -> None:
    """The R-380 outage class: refresh_control_plane fails BETWEEN stop_ and
    start_services_after_transition. The failure must still propagate (rc 1),
    the tier must be back up first, and the release units must NOT be
    installed on a failed transition."""
    result = _restart_services(tmp_path, refresh_rc=1)

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.started, result.calls  # type: ignore[attr-defined]
    assert result.calls == [  # type: ignore[attr-defined]
        "prepare_release_transition",
        "payload_paths_changed",
        "stop_services_for_transition",
        "activate_staged_release",
        "refresh_control_plane",
        "start_services_after_transition",
    ], result.stdout + result.stderr


def test_restart_services_installs_units_only_after_a_successful_refresh(tmp_path) -> None:
    """Control: the green path restarts the tier and then installs units."""
    result = _restart_services(tmp_path, refresh_rc=0)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.calls == [  # type: ignore[attr-defined]
        "prepare_release_transition",
        "payload_paths_changed",
        "stop_services_for_transition",
        "activate_staged_release",
        "refresh_control_plane",
        "start_services_after_transition",
        "install_release_units",
    ], result.stdout + result.stderr


def test_restart_services_failure_arm_is_shaped_to_restart_before_returning() -> None:
    """Structural smoke over the comment-stripped body. Text order is not a
    contract (a `return 1` ahead of a dead restart call satisfies it); the
    behavioural test above is."""
    restart = _strip_comments(function_body(DEPLOY.read_text(encoding="utf-8"), "restart_services"))
    stop_at = restart.index("stop_services_for_transition")
    tail = restart[stop_at:]
    assert "refresh_control_plane || return 1" not in tail, (
        "the post-stop refresh still propagates without restarting the app tier"
    )
    # The failure arm must itself restart the tier before it propagates.
    assert re.search(
        r"if ! refresh_control_plane; then(?:(?!\bfi\b).)*start_services_after_transition",
        tail,
        re.DOTALL,
    ), tail


# --- B. the drop-in gate runs on the path that actually installs --------------


def _dropin_gate_arm(helper_text: str) -> str:
    """The rules the every-deploy install path enforces on a drop-in body."""
    body = _strip_comments(function_body(helper_text, "refresh_install_file"))
    match = re.search(r"\*/radon-\*\.service\.d/\*\.conf\)\s*\n(.*?);;", body, re.DOTALL)
    assert match, "refresh_install_file has no drop-in arm"
    assert "dropin_body_is_valid" in match.group(1)
    return _strip_comments(function_body(helper_text, "dropin_body_is_valid"))


def test_refresh_install_file_gates_dropin_content() -> None:
    arm = _dropin_gate_arm(ROOT_HELPER.read_text(encoding="utf-8"))
    # The pattern, not the bare word: the refusal message names both types, so
    # asserting "Type=simple" passed on the echo line alone.
    assert "Type=(simple|notify)" in arm
    assert "radon-app-runtime" in arm
    assert "ExecStartPre=" in arm
    assert "radon-ib-gateway" in arm
    assert "/home/radon" in arm


@pytest.mark.parametrize(
    "mutation",
    [
        "ExecStart=/home/radon/evil.sh\n",
        "ExecStart=/home/radon/radon/.venv/bin/python -c pass\n",
    ],
)
def test_a_dropin_executing_from_the_checkout_is_refused(tmp_path, mutation) -> None:
    box = Sandbox(tmp_path)
    source = box.cloud / API_DROPIN
    source.write_text(source.read_text(encoding="utf-8") + mutation, encoding="utf-8")
    installed = box.rootfs / "etc/systemd/system/radon-api.service.d/runtime-container.conf"
    before = installed.read_bytes()

    result = box.run()

    assert result.returncode != 0, result.stdout
    assert installed.read_bytes() == before


def test_a_dropin_that_drops_the_execstart_reset_is_refused(tmp_path) -> None:
    box = Sandbox(tmp_path)
    source = box.cloud / API_DROPIN
    text = source.read_text(encoding="utf-8").replace("ExecStartPre=\n", "")
    source.write_text(text, encoding="utf-8")
    installed = box.rootfs / "etc/systemd/system/radon-api.service.d/runtime-container.conf"
    before = installed.read_bytes()

    result = box.run()

    assert result.returncode != 0, result.stdout
    assert installed.read_bytes() == before


def test_an_unchanged_dropin_still_installs_cleanly(tmp_path) -> None:
    """Control: the real shipped drop-in passes the gate."""
    box = Sandbox(tmp_path)
    box.mutate_source(API_DROPIN)
    result = box.run()
    assert result.returncode == 0, result.stderr
    installed = box.rootfs / "etc/systemd/system/radon-api.service.d/runtime-container.conf"
    assert installed.read_text(encoding="utf-8") == (box.cloud / API_DROPIN).read_text(encoding="utf-8")


def test_the_two_privileged_gates_enforce_the_same_dropin_rules() -> None:
    """bootstrap and the deploy helper must not drift apart on this check."""
    arm = _dropin_gate_arm(ROOT_HELPER.read_text(encoding="utf-8"))
    bootstrap = _strip_comments(
        (CLOUD_ROOT / "scripts" / "bootstrap-control-plane.sh").read_text(encoding="utf-8")
    )
    match = re.search(r"\n\s*dropin\)\s*\n(.*?);;", bootstrap, re.DOTALL)
    assert match, "bootstrap has no dropin validator"
    boot_arm = match.group(1)
    for rule in (
        "Type=(simple|notify)", "radon-app-runtime", "ExecStartPre=",
        "radon-ib-gateway", "/home/radon",
    ):
        assert rule in arm, rule
        assert rule in boot_arm, rule


def _run_shipped_dropin_gate(target: pathlib.Path) -> subprocess.CompletedProcess:
    """Run the SHIPPED `dropin_body_is_valid` body against one file."""
    body = function_body(ROOT_HELPER.read_text(encoding="utf-8"), "dropin_body_is_valid")
    script = f"set -uo pipefail\ndropin_body_is_valid() {{\n{body}\n}}\ndropin_body_is_valid \"$1\"\n"
    return subprocess.run(
        ["bash", "-c", script, "bash", str(target)],
        capture_output=True,
        text=True,
    )


def _manifested_dropins() -> list[str]:
    """`services/*.service.d/*.conf` entries in the bootstrap manifest.

    Not a glob over the tree: `services/radon-.service.d/common.conf` is the
    systemd prefix-glob fleet drop-in that `setup-vps.sh` copies directly, and
    it never reaches this gate.
    """
    bootstrap = (CLOUD_ROOT / "scripts" / "bootstrap-control-plane.sh").read_text(
        encoding="utf-8"
    )
    return sorted(set(re.findall(r"^\s*(services/\S+\.service\.d/\S+\.conf)\s*$", bootstrap, re.M)))


@pytest.mark.parametrize("relative", _manifested_dropins(), ids=lambda r: r.split("/")[1])
def test_every_shipped_dropin_passes_the_privileged_gate(relative) -> None:
    """What ships must install.

    R-391 moved the monitor and relay drop-ins to `Type=notify` + `WatchdogSec`
    — forcing `Type=simple` there made systemd stop requiring keepalives, so a
    relay with a dead socket sat `active (running)` forever. Both privileged
    gates still hard-required `Type=simple`, so root bootstrap and the
    every-deploy `refresh_install_file` arm would refuse two of the five
    drop-ins the repo actually ships. Nothing ran the gate against them.
    """
    result = _run_shipped_dropin_gate(CLOUD_ROOT / relative)
    assert result.returncode == 0, result.stderr


def test_bootstrap_stages_dropins_for_systemd_analyze() -> None:
    """A drop-in only reaches `systemd-analyze verify` beside its base unit."""
    bootstrap = _strip_comments(
        (CLOUD_ROOT / "scripts" / "bootstrap-control-plane.sh").read_text(encoding="utf-8")
    )
    match = re.search(r"\n\s*dropin\)\s*\n(.*?);;", bootstrap, re.DOTALL)
    assert match
    assert "SYSTEMD_CANDIDATES" in match.group(1)


# --- C. the root-execution guard can see drop-ins ----------------------------


def test_unit_texts_merges_dropins_into_their_base_unit(tmp_path) -> None:
    services = tmp_path / "services"
    (services / "radon-x.service.d").mkdir(parents=True)
    (services / "radon-x.service").write_text(
        "[Service]\nUser=radon\nExecStart=/home/radon/radon/.venv/bin/python /home/radon/x.py\n",
        encoding="utf-8",
    )
    (services / "radon-x.service.d" / "runtime.conf").write_text(
        "[Service]\nUser=root\nExecStart=\nExecStart=/home/radon/x.sh\n",
        encoding="utf-8",
    )

    units = rootpaths._unit_texts(services)
    assert "radon-x.service" in units
    offenders = rootpaths._checkout_payload_offenders(
        {n: t for n, t in units.items() if rootpaths._runs_as_root(t)}
    )
    assert offenders == {"radon-x.service": ["/home/radon/x.sh"]}


def test_a_dropin_execstart_reset_clears_the_base_units_checkout_payload(tmp_path) -> None:
    """The shipped shape: root, but executing only the root-owned runtime."""
    services = tmp_path / "services"
    (services / "radon-y.service.d").mkdir(parents=True)
    (services / "radon-y.service").write_text(
        "[Service]\nUser=radon\n"
        "ExecStartPre=/home/radon/radon/.venv/bin/python3.13 /home/radon/radon/scripts/db/migrate.py\n"
        "ExecStart=/home/radon/radon/.venv/bin/uvicorn scripts.api.server:app\n",
        encoding="utf-8",
    )
    (services / "radon-y.service.d" / "runtime.conf").write_text(
        "[Service]\nUser=root\nType=simple\nExecStartPre=\nExecStart=\n"
        "ExecStart=/usr/local/sbin/radon-app-runtime run %n\n",
        encoding="utf-8",
    )

    units = rootpaths._unit_texts(services)
    offenders = rootpaths._checkout_payload_offenders(
        {n: t for n, t in units.items() if rootpaths._runs_as_root(t)}
    )
    assert offenders == {}


def test_the_shipped_tree_still_passes_the_root_execution_guard() -> None:
    """With drop-ins merged, the real cloud/services tree must stay clean."""
    units = rootpaths._unit_texts()
    root_units = {n: t for n, t in units.items() if rootpaths._runs_as_root(t)}
    assert "radon-api.service" in root_units, "the drop-in makes radon-api a root unit"
    assert rootpaths._checkout_payload_offenders(root_units) == {}
