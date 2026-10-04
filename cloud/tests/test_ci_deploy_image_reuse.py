"""CIP-016: the gated fallback deploy may reuse only a proven exact image."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "cloud/scripts/deploy.sh"


def _final_invocation() -> str:
    workflow = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    job = workflow["jobs"]["deploy"]
    assert "app-images" in job["needs"]
    assert "needs.app-images.result == 'success'" in job["if"]
    script = next(s for s in job["steps"] if s.get("name") == "Deploy via SSH")["with"]["script"]
    # Execute the real workflow tail after recovery and control-plane sync.
    # A source grep for the flag would also pass if it were never exported.
    tail = script.split('bash "$RUNNER/cloud/scripts/sync-control-plane.sh"\n', 1)[1]
    return tail.split("fi\n", 1)[1].split('"$RUNNER/cloud/scripts/prune-deploy-runners.py"', 1)[0]


@pytest.mark.parametrize("proof", ["valid", "drift", "reload", "host", "exec", "unloaded", "no-build", "bad-sha", "pull-failed"])
def test_final_deploy_reuses_proven_image_or_compiles_host_bundle(
    tmp_path: Path, proof: str
) -> None:
    sha = "d" * 40
    staged = tmp_path / "staged"
    target = staged / "cloud/services/radon-nextjs.service.d/runtime-container.conf"
    target.parent.mkdir(parents=True)
    dropin = (ROOT / "cloud/services/radon-nextjs.service.d/runtime-container.conf").read_text()
    target.write_text(dropin)
    installed = tmp_path / "installed.conf"
    installed.write_text(dropin + ("# drift\n" if proof == "drift" else ""))
    (staged / "web/.next").mkdir(parents=True)
    if proof != "no-build":
        (staged / "web/.next/BUILD_ID").write_text("retained-rollback-build\n")
    for name in ("package.json", "bun.lock", "web/package.json", "web/bun.lock"):
        (staged / name).write_text("fixture\n")
    browsers = tmp_path / "browsers"
    browsers.mkdir()
    (browsers / "installed").touch()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    def executable(path: Path, body: str) -> None:
        path.write_text("#!/bin/bash\nset -eu\n" + body)
        path.chmod(0o755)

    executable(fake_bin / "git", f"printf '%s\\n' {'short' if proof == 'bad-sha' else sha}\n")
    executable(fake_bin / "bun", '''if [[ "$*" == "--version" ]]; then
  echo 1.3.14
else
  printf 'bun %s\\n' "$*" >> "$CALL_LOG"
fi
''')
    executable(fake_bin / "systemctl", f'''case "$*" in
  *--property=DropInPaths*) echo {'/unrelated.conf' if proof == 'unloaded' else shlex.quote(str(installed))} ;;
  *--property=NeedDaemonReload*) echo {'yes' if proof == 'reload' else 'no'} ;;
  *--property=Environment*) echo RADON_RUNTIME={'host' if proof == 'host' else 'container'} ;;
  *--property=ExecStart*) echo {'/usr/bin/bun run start' if proof == 'exec' else '/usr/local/sbin/radon-app-runtime run %n'} ;;
  *) exit 2 ;;
esac
''')
    runner = tmp_path / "runner/cloud/scripts"
    runner.mkdir(parents=True)
    executable(runner / "deploy.sh", f'''
[[ "$1" == "{sha}" ]]
source {shlex.quote(str(DEPLOY))}
write_web_env() {{ :; }}
seed_staged_node_modules() {{ SEEDED_ROOT_NODE_MODULES=1; SEEDED_WEB_NODE_MODULES=1; }}
should_rebuild_next() {{ return 0; }}
run_with_cloud_env() {{ shift; "$@"; }}
prepull_app_images() {{
  printf 'pull %s\\n' "$1" >> "$CALL_LOG"
  return {1 if proof == 'pull-failed' else 0}
}}
build_nextjs_at "$STAGED"
''')
    log = tmp_path / "calls"
    env = {k: v for k, v in os.environ.items() if k != "RADON_DEPLOY_USE_NODE_IMAGE_BUILD"}
    env.update(
        PATH=f"{fake_bin}:{os.environ['PATH']}",
        RUNNER=str(tmp_path / "runner"), SHA=sha, STAGED=str(staged),
        RADON_DIR=str(tmp_path / "live"),
        RADON_NEXT_RUNTIME_DROPIN=str(installed),
        RADON_SYSTEMCTL=str(fake_bin / "systemctl"),
        PLAYWRIGHT_BROWSERS_PATH=str(browsers), CALL_LOG=str(log),
    )
    result = subprocess.run(
        ["bash", "-eu", "-c", _final_invocation()], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = log.read_text().splitlines()
    if proof == "valid":
        assert calls == [f"pull {sha}"]
        assert "exact node image is the canonical production artifact" in result.stdout
    elif proof == "pull-failed":
        assert calls == [f"pull {sha}", "bun run build"]
    else:
        assert calls == ["bun run build"]
