"""Exercise the immutable image's module entrypoint without pytest path setup."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('operation',[
    'assert server.Gateway',
    '''from scripts.model_gateway import _ocr_backend
server.engine._text_from_openai=lambda value: "same engine"
assert _ocr_backend._openai_text({})=="same engine"''',
    '''from scripts.model_gateway import _catalog_backend
assert _catalog_backend._ladder() is server.engine''',
    '''from scripts.model_gateway import _x_search_backend
from types import SimpleNamespace
server.engine.subscription_auth=lambda name: SimpleNamespace(token="fixture")
assert _x_search_backend._xai_token()=="fixture"''',
    '''from scripts.model_gateway import _embedding_backend, _clairic_backend
assert _embedding_backend._nvidia_embed([],input_type="passage",post=None)==[]
assert _clairic_backend.ClairicError is server.ClairicError
assert "model_gateway.engine" not in sys.modules''',
])
def test_image_package_entrypoint_and_backends_share_one_engine(tmp_path,operation):
    code='''import sys
from pathlib import Path
assert str(Path.cwd()/"scripts") not in sys.path
from scripts.model_gateway import server
'''+operation
    result=subprocess.run([sys.executable,'-E','-s','-c',code],cwd=ROOT,
        env={'PATH':os.defpath,'HOME':str(tmp_path)},capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_gateway_build_smokes_actual_package_entrypoint_without_network():
    text=(ROOT/'docker/app/Dockerfile.python').read_text()
    smoke=text.split("<<'PY_FORECAST'",1)[1].split('PY_FORECAST',1)[0]
    assert 'from scripts.model_gateway import server' in smoke
    assert "RUN --network=none python - <<'PY_FORECAST'" in text
