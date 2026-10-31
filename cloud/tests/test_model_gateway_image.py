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


def test_gateway_image_preserves_forecasting_outside_the_ordinary_python_image():
    text = (ROOT / "docker/app/Dockerfile.python").read_text()
    assert "AS runtime" in text
    assert "FROM runtime AS model-gateway" in text
    assert text.rstrip().endswith("FROM runtime AS python\nUSER radon")
    gateway = text.split("FROM runtime AS model-gateway", 1)[1].split("FROM runtime AS python", 1)[0]
    assert "--index-url https://download.pytorch.org/whl/cpu" in gateway
    assert "torch==2.12.1+cpu" in gateway
    assert "chronos-forecasting==2.3.0" in gateway
    assert "import chronos, torch" in gateway
    assert "USER radon" in gateway


def test_both_pr_and_release_build_and_smoke_the_gateway_image():
    wf = yaml.safe_load((ROOT / ".github/workflows/app-images.yml").read_text())
    job = wf['jobs']['python-image']
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
