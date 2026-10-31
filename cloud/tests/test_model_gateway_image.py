"""A gateway-only immutable image preserves live optional forecasting models."""
from pathlib import Path
import re
import yaml

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
