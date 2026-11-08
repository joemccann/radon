"""A gateway-only immutable image preserves live optional forecasting models."""
from pathlib import Path
import re
import yaml
import pytest

from test_app_runtime import CLOUD, TEST_SHA, _run, _run_line

ROOT = CLOUD.parent
GATEWAY_REPO = "ghcr.io/joemccann/radon-model-gateway"


def test_gateway_uses_its_own_exact_release_image(tmp_path):
    result = _run(tmp_path, ["run", "radon-model-gateway.service"])
    assert result.returncode == 0, result.stderr
    assert f"{GATEWAY_REPO}:{TEST_SHA} python -m scripts.model_gateway.server" in _run_line(result)


def test_release_admission_refuses_a_missing_gateway_image(tmp_path):
    result = _run(tmp_path, ["pull", TEST_SHA], extra_env={"RADON_TEST_MISSING_TAGS": f"{GATEWAY_REPO}:{TEST_SHA}"})
    assert result.returncode == 69
    assert "incomplete" in result.stderr or "failed" in result.stderr


def test_gateway_torch_layer_does_not_follow_copy_scripts():
    """CIP-025: a scripts-only push must not rebuild torch.

    Mixed rebuild 41949521130 (job 126990250055) paid 38.7s pip plus 79s
    image export inside a 232s gateway step because `FROM runtime AS
    model-gateway` inherited `COPY scripts ./scripts`. Torch belongs on a
    scripts-free `runtime-deps` parent; the gateway copies scripts after
    the lock install.
    """
    text = (ROOT / "docker/app/Dockerfile.python").read_text()
    assert "FROM runtime-deps AS runtime" in text
    assert "FROM runtime-deps AS model-gateway" in text
    gateway = text.split("FROM runtime-deps AS model-gateway", 1)[1].split(
        "FROM runtime AS python", 1
    )[0]
    torch_at = gateway.index("-r requirements-model-gateway.txt")
    scripts_at = gateway.index("COPY scripts ./scripts")
    assert torch_at < scripts_at
    runtime = text.split("FROM runtime-deps AS runtime", 1)[1].split(
        "FROM runtime-deps AS model-gateway", 1
    )[0]
    assert "COPY scripts ./scripts" in runtime
    deps = text.split("AS runtime-deps", 1)[1].split("FROM runtime-deps AS runtime", 1)[0]
    assert "COPY scripts ./scripts" not in deps


def test_gateway_image_preserves_forecasting_outside_the_ordinary_python_image():
    text = (ROOT / "docker/app/Dockerfile.python").read_text()
    assert "AS runtime" in text
    assert "FROM runtime-deps AS model-gateway" in text
    assert text.rstrip().endswith("FROM runtime AS python\nUSER radon")
    gateway = text.split("FROM runtime-deps AS model-gateway", 1)[1].split("FROM runtime AS python", 1)[0]
    assert "--index-url https://download.pytorch.org/whl/cpu" in gateway
    # DS-2026-10-28-02: the forecasting closure installs from a hash lock.
    assert "--require-hashes --no-deps" in gateway
    assert "-r requirements-model-gateway.txt" in gateway
    lock = (ROOT / "docker/app/requirements-model-gateway.txt").read_text()
    assert re.search(r"^torch==2\.12\.1\+cpu \\$", lock, re.M)
    assert re.search(r"^chronos-forecasting==2\.3\.0 \\$", lock, re.M)
    assert re.search(r"^scipy==1\.17\.1 \\$", lock, re.M)
    assert "import chronos, torch" in gateway
    assert "USER radon" in gateway


def test_both_pr_and_release_build_and_smoke_the_gateway_image():
    wf = yaml.safe_load((ROOT / ".github/workflows/app-images.yml").read_text())
    job = wf['jobs']['gateway-image']
    build = next(s for s in job['steps'] if s.get('with', {}).get('target') == 'model-gateway')
    assert build['with']['file'] == 'docker/app/Dockerfile.python'
    assert f"{GATEWAY_REPO}:${{{{ github.sha }}}}" in build['with']['tags']
    assert build['with']['push'] == "${{ github.ref == 'refs/heads/main' && github.event_name == 'push' }}"
    assert build['if'] == "steps.reuse-gateway.outputs.hit != 'true'"
    smoke = next(s for s in job['steps'] if s.get('name') == 'Smoke model gateway runtime')
    assert 'import chronos, torch' in smoke['run']
    assert '--network none' in smoke['run']
    assert '--user 1000:1000' in smoke['run']


def test_gateway_status_identity_uses_exact_image_not_caller_or_environment(tmp_path):
    source=tmp_path/'source.env'
    stale='a'*40
    source.write_text('NODE_ENV=production\nRADON_DEPLOY_SHA='+stale+'\n')
    result=_run(tmp_path,['run','radon-model-gateway.service'],extra_env={'RADON_DEPLOY_SHA':stale,'RADON_TEST_ENV_FILE':str(source)})
    assert result.returncode==0,result.stderr
    args=_run_line(result).split()
    assert f'RADON_DEPLOY_SHA={TEST_SHA}' in args
    assert f'RADON_DEPLOY_SHA={stale}' not in args
    assert 'RADON_DEPLOY_SHA' not in args
    rendered=Path(args[args.index('--env-file')+1]).read_text()
    assert 'RADON_DEPLOY_SHA=' not in rendered


def _run_selected_gateway(tmp_path, image):
    """Run shipped cmd_run while making any second HEAD/tag lookup fail."""
    import subprocess
    from test_app_runtime import RUNTIME,_runtime_env
    log=tmp_path/'docker.log';docker=tmp_path/'docker'
    docker.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> '+str(log)+'\n');docker.chmod(0o755)
    library=tmp_path/'runtime-library.sh'
    library.write_text(RUNTIME.read_text().split('[[ $# -ge 1 ]] || usage',1)[0])
    env=_runtime_env(tmp_path,docker)
    env.pop('NOTIFY_SOCKET',None)
    env['RESOLVED_IMAGE']=image
    lookup=tmp_path/'unexpected-lookup'
    env['LOOKUP_MARKER']=str(lookup)
    script='''set -euo pipefail
source "$1"
model_gateway_image() { printf '%s\\n' "$RESOLVED_IMAGE"; }
image_tag() { printf reread > "$LOOKUP_MARKER"; return 69; }
cmd_run radon-model-gateway.service
'''
    result=subprocess.run(['bash','-c',script,'bash',str(library)],env=env,capture_output=True,text=True,timeout=20)
    result.docker_log=log
    return result,lookup


def test_gateway_identity_is_derived_from_selected_image_without_second_head_lookup(tmp_path):
    selected='b'*40
    result,lookup=_run_selected_gateway(tmp_path,f'{GATEWAY_REPO}:{selected}')
    assert result.returncode==0,result.stderr
    assert f'--env RADON_DEPLOY_SHA={selected}' in _run_line(result)
    assert not lookup.exists()


@pytest.mark.parametrize('image',[GATEWAY_REPO+':latest',GATEWAY_REPO+'@sha256:'+'a'*64,'ghcr.io/joemccann/radon-python:'+'a'*40,GATEWAY_REPO+':'+'A'*40])
def test_gateway_refuses_noncanonical_selected_image_before_launch(tmp_path,image):
    result,lookup=_run_selected_gateway(tmp_path,image)
    assert result.returncode==69,result.stdout+result.stderr
    assert not result.docker_log.exists()
    assert not lookup.exists()


GATEWAY_REUSE = "Reuse identical model gateway image"
GATEWAY_HASH = "a" * 64
GATEWAY_SHA = "c" * 40


def _gateway_steps():
    wf = yaml.safe_load((ROOT / ".github/workflows/app-images.yml").read_text())
    return wf["jobs"]["gateway-image"]["steps"]


def _run_gateway_reuse(tmp_path, *, event, ref, inspect_status, digest=GATEWAY_HASH):
    """Run the shipped gateway reuse script with a docker that logs every call."""
    import os
    import subprocess
    step = next(s for s in _gateway_steps() if s.get("name") == GATEWAY_REUSE)
    script = step["run"].replace("${{ steps.pyimage.outputs.hash }}", digest)
    script = script.replace("${{ github.sha }}", GATEWAY_SHA)
    assert "${{" not in script, "new interpolation: teach this fixture its value"
    log = tmp_path / "docker.log"
    output = tmp_path / "github-output"
    docker = tmp_path / "bin" / "docker"
    docker.parent.mkdir()
    docker.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" >> "$CALL_LOG"\n'
        'if [ "$3" = inspect ] && [ "${5:-}" = --raw ]; then echo manifest; exit 0; fi\n'
        'if [ "$3" = inspect ]; then exit "$INSPECT_STATUS"; fi\n'
        "exit 0\n"
    )
    docker.chmod(0o755)
    env = {
        "PATH": f"{docker.parent}{os.pathsep}{os.environ['PATH']}",
        "CALL_LOG": str(log),
        "INSPECT_STATUS": str(inspect_status),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REF": ref,
    }
    completed = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        env=env, capture_output=True, text=True, check=False,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return completed, calls, output.read_text() if output.exists() else ""


def test_gateway_reuse_runs_on_main_push_and_pull_requests_only():
    step = next(s for s in _gateway_steps() if s.get("name") == GATEWAY_REUSE)
    assert step["id"] == "reuse-gateway"
    # A PR that edits this workflow or cloud/scripts/model_clients.py (both
    # outside the input hash) skips the lookup and runs the bounds check.
    assert step["if"] == (
        "(github.ref == 'refs/heads/main' && github.event_name == 'push')"
        " || (github.event_name == 'pull_request'"
        " && steps.unhashed.outputs.gateway == 'unchanged')"
    )


@pytest.mark.parametrize(("inspect_status", "hit"), [(0, "true"), (1, "false")])
def test_pull_request_gateway_reuse_only_reads_the_input_hash_tag(tmp_path, inspect_status, hit):
    """A PR with main's exact inputs skips the build, smoke and bounds check
    main already ran on them. No packages:write token: never a tag write."""
    completed, calls, output = _run_gateway_reuse(
        tmp_path, event="pull_request", ref="refs/pull/7/merge", inspect_status=inspect_status
    )
    assert completed.returncode == 0, completed.stderr
    assert calls == [f"buildx imagetools inspect {GATEWAY_REPO}:ihash-{GATEWAY_HASH}"]
    assert output == f"hit={hit}\n"


@pytest.mark.parametrize("event", ["pull_request", "push"])
def test_gateway_reuse_without_a_valid_hash_builds_without_looking(tmp_path, event):
    completed, calls, output = _run_gateway_reuse(
        tmp_path, event=event, ref="refs/heads/main", inspect_status=0, digest="not-a-hash"
    )
    assert completed.returncode == 0, completed.stderr
    assert calls == []
    assert output == "hit=false\n"


def test_main_push_gateway_reuse_still_retags_and_compares_manifest_bytes(tmp_path):
    completed, calls, output = _run_gateway_reuse(
        tmp_path, event="push", ref="refs/heads/main", inspect_status=0
    )
    assert completed.returncode == 0, completed.stderr
    source = f"{GATEWAY_REPO}:ihash-{GATEWAY_HASH}"
    target = f"{GATEWAY_REPO}:{GATEWAY_SHA}"
    assert calls == [
        f"buildx imagetools inspect {source}",
        f"buildx imagetools inspect {source} --raw",
        f"buildx imagetools create -t {target} -t {GATEWAY_REPO}:latest {source}",
        f"buildx imagetools inspect {target} --raw",
    ]
    assert output == "hit=true\n"


def test_gateway_build_reads_registry_cache_and_exports_only_on_main_push():
    build = next(s for s in _gateway_steps() if s.get("with", {}).get("target") == "model-gateway")
    assert build["with"]["cache-from"].splitlines() == [
        f"type=registry,ref={GATEWAY_REPO}:buildcache",
        "type=registry,ref=ghcr.io/joemccann/radon-python:buildcache",
        "type=gha,scope=radon-model-gateway",
        "type=gha,scope=radon-python",
    ]
    # Pull requests export nothing: the GHA export was 288.5s of a 490s
    # gateway build step on PR run 41261010191.
    assert build["with"]["cache-to"] == (
        "${{ github.ref == 'refs/heads/main' && github.event_name == 'push' && "
        f"'type=registry,ref={GATEWAY_REPO}:buildcache,mode=max' || '' }}}}"
    )


def test_no_image_build_exports_to_the_actions_cache():
    text = (ROOT / ".github/workflows/app-images.yml").read_text()
    assert "type=gha,mode=" not in text
    assert "cache_to=type=gha" not in text
