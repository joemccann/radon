"""Hosted-runner disk before the app image load.

Private validation runs on the smaller GitHub-hosted runner. The python
image is loaded into the daemon (`load: true`) and the import fails with
ENOSPC when Android, .NET, GHC, GHCup and CodeQL are still on disk.
`app-images-pr.yml` has no build steps of its own; it calls this workflow.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "app-images.yml"
PR_WORKFLOW = REPO / ".github" / "workflows" / "app-images-pr.yml"
BUILDX = "docker/setup-buildx-action@"
DISK_TARGETS = (
    "/usr/local/lib/android",
    "/usr/share/dotnet",
    "/opt/ghc",
    "/usr/local/.ghcup",
    "/opt/hostedtoolcache/CodeQL",
)


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _disk_step(job_name: str) -> dict:
    steps = _jobs()[job_name]["steps"]
    buildx = next(
        index
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith(BUILDX)
    )
    step = steps[buildx - 1]
    assert step.get("uses") is None
    assert "if" not in step
    return step


def test_pr_caller_builds_only_through_the_reusable_workflow() -> None:
    parsed = yaml.safe_load(PR_WORKFLOW.read_text(encoding="utf-8"))
    jobs = parsed["jobs"]
    assert jobs
    for job in jobs.values():
        assert "steps" not in job
        assert job["uses"] == "./.github/workflows/app-images.yml"


def test_each_image_job_frees_disk_immediately_before_buildx() -> None:
    scripts = []
    for job_name in ("python-image", "node-image"):
        step = _disk_step(job_name)
        script = step["run"]
        scripts.append(script)
        lines = [line.strip() for line in script.strip().splitlines() if line.strip()]
        assert lines[0] == "df -h /"
        assert lines[-1] == "df -h /"
        assert script.count("df -h /") == 2
        for target in DISK_TARGETS:
            assert f"sudo rm -rf {target} || true" in script
        assert "docker system prune -af || true" in script
        assert "rm -rf /opt/hostedtoolcache ||" not in script
        assert "rm -rf /opt/hostedtoolcache\n" not in script
    assert scripts[0] == scripts[1]


def test_cleanup_is_fail_open_and_idempotent(tmp_path: Path) -> None:
    script = _disk_step("python-image")["run"]
    root = tmp_path / "runner"
    log = tmp_path / "calls.log"
    rewritten = script
    for target in DISK_TARGETS:
        standin = root.joinpath(*target.strip("/").split("/"))
        standin.parent.mkdir(parents=True, exist_ok=True)
        standin.mkdir()
        (standin / "blob").write_text("preinstalled", encoding="utf-8")
        rewritten = rewritten.replace(target, str(standin), 1)
    for target in DISK_TARGETS:
        assert f"rm -rf {target} ||" not in rewritten
    rewritten = rewritten.replace("sudo rm -rf", "rm -rf")
    assert "sudo " not in rewritten
    prelude = (
        "df() { echo \"df $*\" >> \"$CALL_LOG\"; }\n"
        "rm() {\n"
        "  echo \"rm $*\" >> \"$CALL_LOG\"\n"
        "  case \"$*\" in\n"
        "    *dotnet*) return 1 ;;\n"
        "  esac\n"
        "  command rm \"$@\"\n"
        "}\n"
        "docker() { echo \"docker $*\" >> \"$CALL_LOG\"; return 1; }\n"
    )
    env = os.environ.copy()
    env["CALL_LOG"] = str(log)
    completed = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", prelude + rewritten],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls[0] == "df -h /"
    assert calls[-1] == "df -h /"
    assert "docker system prune -af" in calls
    assert any("dotnet" in line and line.startswith("rm ") for line in calls)
    dotnet = root / "usr" / "share" / "dotnet"
    assert dotnet.is_dir()
    for target in DISK_TARGETS:
        if target.endswith("/dotnet"):
            continue
        standin = root.joinpath(*target.strip("/").split("/"))
        assert not standin.exists(), standin
    log.unlink()
    again = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", prelude + rewritten],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert again.returncode == 0, again.stderr


# 40 GiB, in KiB. Matches the workflow threshold.
_ENOUGH_FREE_KIB = 40 * 1024 * 1024


def _run_disk_script(tmp_path: Path, avail_kb: str | None) -> tuple[subprocess.CompletedProcess[str], str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = _disk_step("python-image")["run"]
    root = tmp_path / "runner"
    log = tmp_path / "calls.log"
    rewritten = script
    for target in DISK_TARGETS:
        standin = root.joinpath(*target.strip("/").split("/"))
        standin.parent.mkdir(parents=True, exist_ok=True)
        standin.mkdir()
        (standin / "blob").write_text("preinstalled", encoding="utf-8")
        rewritten = rewritten.replace(target, str(standin), 1)
    rewritten = rewritten.replace("sudo rm -rf", "rm -rf")
    prelude = (
        "df() {\n"
        "  echo \"df $*\" >> \"$CALL_LOG\"\n"
        "  if [ \"$1\" = \"-Pk\" ]; then\n"
        "    printf 'Filesystem 1024-blocks Used Available Capacity Mounted\\n'\n"
        "    printf 'root 1 1 %s 1%% /\\n' \"${DF_AVAIL_KB-}\"\n"
        "  fi\n"
        "}\n"
        "rm() { echo \"rm $*\" >> \"$CALL_LOG\"; command rm \"$@\"; }\n"
        "docker() { echo \"docker $*\" >> \"$CALL_LOG\"; }\n"
    )
    env = os.environ.copy()
    env["CALL_LOG"] = str(log)
    if avail_kb is None:
        env.pop("DF_AVAIL_KB", None)
    else:
        env["DF_AVAIL_KB"] = avail_kb
    completed = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", prelude + rewritten],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    return completed, calls


def test_large_disk_skips_the_toolchain_delete(tmp_path: Path) -> None:
    """Main runners already have ~86 GiB free. The delete must not run there."""
    script = _disk_step("python-image")["run"]
    assert "threshold_kb=$((40 * 1024 * 1024))" in script
    completed, calls = _run_disk_script(tmp_path, str(_ENOUGH_FREE_KIB))
    assert completed.returncode == 0, completed.stderr
    assert "rm -rf" not in calls
    assert "docker system prune" not in calls
    root = tmp_path / "runner"
    for target in DISK_TARGETS:
        standin = root.joinpath(*target.strip("/").split("/"))
        assert standin.is_dir(), standin


def test_small_or_unknown_disk_still_deletes_preinstalled_toolchains(tmp_path: Path) -> None:
    """The 7.7GB hosted runner, and a df we cannot parse, still free space."""
    small, small_calls = _run_disk_script(tmp_path / "small", "3000000")
    assert small.returncode == 0, small.stderr
    assert "docker system prune -af" in small_calls
    for target in DISK_TARGETS:
        standin = (tmp_path / "small" / "runner").joinpath(*target.strip("/").split("/"))
        assert not standin.exists(), standin

    unknown, unknown_calls = _run_disk_script(tmp_path / "unknown", None)
    assert unknown.returncode == 0, unknown.stderr
    assert "docker system prune -af" in unknown_calls
    for target in DISK_TARGETS:
        standin = (tmp_path / "unknown" / "runner").joinpath(*target.strip("/").split("/"))
        assert not standin.exists(), standin
