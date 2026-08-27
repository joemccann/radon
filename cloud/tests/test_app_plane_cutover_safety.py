"""The container cutover and the no-restart promote path must be safe.

R-231: the no-restart promote branch calls `activate_staged_release` WITHOUT
`stop_services_for_transition`, and inside it `reuse_venv` is set only when
`live_python_env_matches` succeeds — which requires `.radon-req-hash`, a file
only `install_target_python_env` ever writes, so on the production host today
it does not exist. The branch therefore moves `.venv` out from under running
units, `git reset --hard`s the live checkout and rebuilds the venv while
radon-api, radon-monitor and every `.venv/bin/python` timer oneshot are
running. The same branch sets `UNITS_RESTARTED=0`, which forces `remaining=0`
in `check_units_stable`, so the settle window is skipped and the deploy is
green with zero stability time.

R-232: `cmd_run` uses `--cgroup-parent=system.slice`, so the container's
processes land in `system.slice/docker-<id>.scope` and are NOT in the unit's
cgroup — systemd's `KillMode=control-group` sweep reaches only the `docker run`
client. The container survives holding the `--name` and both state bind
mounts, and with no `docker rm -f` the restart hits `Conflict` five times into
`start-limit-hit` while the orphan keeps writing to `data/`.

R-233: the node image declares the three `NEXT_PUBLIC_*` ARGs and builds with
no `--build-arg`, so the pushed image has them inlined as empty strings.
`NEXT_PUBLIC_*` are compile-time inlined, so `--env-file` cannot repair them.

R-234: `image_tag()` pins the exact deploy SHA with no fallback and no
existence preflight, while the image workflow cancels in-progress builds — so
a second push within the build budget leaves SHA1's tags unpushed and all five
app units `docker run` a `manifest unknown`.

R-235: the drop-ins reset `ExecStart=` but not `ExecStartPre=`, so the base
unit's migrate step still runs — as root, against production Turso — and
203/EXECs the moment the host `.venv` is retired.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_caddyfile import read_caddyfile  # noqa: E402,F401  (path bootstrap)

CLOUD = Path(__file__).resolve().parents[1]
DEPLOY = CLOUD / "scripts" / "deploy.sh"
RUNTIME = CLOUD / "scripts" / "radon-app-runtime.sh"
SERVICES = CLOUD / "services"
WORKFLOW = CLOUD.parent / ".github" / "workflows" / "app-images.yml"
DOCKERFILE_NODE = CLOUD.parent / "docker" / "app" / "Dockerfile.node"

NEXT_PUBLIC_ARGS = (
    "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
    "NEXT_PUBLIC_RADON_API_URL",
    "NEXT_PUBLIC_IB_REALTIME_WS_URL",
)


def _function_body(text: str, name: str) -> str:
    start = text.index(f"{name}() {{")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError(f"unterminated {name}")


class TestNoRestartPromoteIsSafe:
    def test_the_no_restart_branch_does_not_move_the_venv_under_running_units(self):
        # Strip comments before slicing: the branch's own comment names
        # `stop_services_for_transition` to contrast with the normal path, and
        # a naive slice ends there instead of at the real call.
        text = "\n".join(
            line for line in DEPLOY.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        branch = text[text.index("No runtime payload changes"):]
        branch = branch[: branch.index("stop_services_for_transition")]
        assert "RADON_DEPLOY_KEEP_LIVE_VENV=1" in branch, (
            "the no-restart path runs activate_staged_release with services up "
            "and no guarantee the .venv move is skipped"
        )

    def test_activate_honours_the_keep_live_venv_contract(self):
        body = _function_body(DEPLOY.read_text(encoding="utf-8"), "activate_staged_release")
        assert "RADON_DEPLOY_KEEP_LIVE_VENV" in body, (
            "activate_staged_release cannot be told to leave the live venv alone"
        )

    def test_the_settle_window_is_not_zeroed_by_the_no_restart_branch(self):
        body = _function_body(DEPLOY.read_text(encoding="utf-8"), "check_units_stable")
        stripped = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("#")
        )
        assert re.search(r"^\s*remaining=0\s*$", stripped, re.M) is None, (
            "UNITS_RESTARTED=0 forces remaining=0, so the promote path that "
            "mutates the live tree is the one path with no settle window"
        )


class TestContainerLifecycleIsSweepable:
    def test_a_stale_container_is_removed_before_run(self):
        body = _function_body(RUNTIME.read_text(encoding="utf-8"), "cmd_run")
        assert "rm -f" in body, (
            "no docker rm -f, so an orphaned container holding the --name "
            "makes every restart Conflict into start-limit-hit"
        )

    @pytest.mark.parametrize(
        "example", sorted(SERVICES.glob("*.service.d/runtime-container.conf.example"))
    )
    def test_every_container_drop_in_reaps_the_container_on_stop(self, example):
        """The cgroup half of R-232 is NOT fixable as the finding proposes.

        Docker's systemd cgroup driver accepts a slice, not a unit path
        (`cloud/tests/test_app_runtime.py` asserts `--cgroup-parent=system.slice`
        for that reason), so the container cannot be placed in the unit's own
        cgroup and `KillMode=control-group` will always reach only the
        `docker run` client. The reachable fix is reaping the container
        explicitly — on the way in via `docker rm -f`, and here on the way out.
        """
        text = example.read_text(encoding="utf-8")
        directives = "\n".join(re.sub(r"^#\s?", "", line) for line in text.splitlines())
        if "ExecStart=/usr/local/sbin/radon-app-runtime" not in directives:
            pytest.skip(f"{example.parent.name} declares no container ExecStart")
        assert "ExecStopPost=/usr/local/sbin/radon-app-runtime stop %n" in directives, (
            f"{example.parent.name} leaves the container running after the unit "
            "stops, holding --name and both state bind mounts"
        )


class TestImageBuildCarriesPublicEnv:
    @pytest.mark.parametrize("name", NEXT_PUBLIC_ARGS)
    def test_the_workflow_passes_each_public_build_arg(self, name):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        assert f"--build-arg {name}=" in workflow or f"{name}=${{" in workflow, (
            f"{name} is declared ARG and promoted to ENV, and NEXT_PUBLIC_* is "
            "compile-time inlined — an unset build arg ships an empty string "
            "into the client bundle that --env-file cannot repair"
        )

    def test_the_dockerfile_still_declares_them(self):
        text = DOCKERFILE_NODE.read_text(encoding="utf-8")
        for name in NEXT_PUBLIC_ARGS:
            assert f"ARG {name}" in text


class TestImageTagIsPreflighted:
    def test_run_checks_the_tag_before_depending_on_it(self):
        text = RUNTIME.read_text(encoding="utf-8")
        assert "manifest inspect" in text or "image_available" in text, (
            "the run path pins the exact deploy SHA with no fallback and no "
            "existence check, so a cancelled image build fails all five units"
        )

    def test_there_is_a_fallback_when_the_sha_tag_is_absent(self):
        text = RUNTIME.read_text(encoding="utf-8")
        assert "RADON_APP_IMAGE_FALLBACK_TAG" in text or ":latest" in text, (
            "no fallback tag, so a cancelled build is an unrecoverable "
            "manifest unknown across api, nextjs, relay, monitor and newsfeed"
        )


class TestDropInsResetExecStartPre:
    @pytest.mark.parametrize(
        "example", sorted(SERVICES.glob("*.service.d/runtime-container.conf.example"))
    )
    def test_every_example_that_resets_execstart_also_resets_execstartpre(self, example):
        text = example.read_text(encoding="utf-8")
        # These files are commented templates; look at the directive lines.
        directives = "\n".join(
            re.sub(r"^#\s?", "", line) for line in text.splitlines()
        )
        if "ExecStart=" not in directives:
            pytest.skip(f"{example.name} declares no ExecStart override")
        base = SERVICES / example.parent.name.replace(".service.d", ".service")
        if base.exists() and "ExecStartPre=" not in base.read_text(encoding="utf-8"):
            pytest.skip(f"{base.name} has no ExecStartPre to reset")
        assert "ExecStartPre=" in directives, (
            f"{example.name} resets ExecStart but leaves the base unit's "
            "ExecStartPre running — as root, against production Turso, and "
            "203/EXEC once the host .venv is retired"
        )
