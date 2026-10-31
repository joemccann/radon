"""Exercise the immutable image's module entrypoint without pytest path setup."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('operation',[
    'assert server.Gateway',
    '''import importlib.abc
class RejectApplicationDatabase(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"db", "scripts.db", "libsql_experimental"} or fullname.startswith(("db.", "scripts.db.")):
            raise ImportError("native catalog must not import the application database")
sys.meta_path.insert(0, RejectApplicationDatabase())
from scripts.model_gateway import _catalog_backend, specialized
assert _catalog_backend._ladder() is server.engine
_catalog_backend.provider_key=lambda provider: None
assert specialized.refresh_catalog({})["rows"]==[]
assert isinstance(specialized.model_inventory(refresh=False), dict)
_catalog_backend.LLM_MODELS_JSON=Path.home()/"absent-catalog.json"
assert _catalog_backend.load_previous()=={}
try:
    _catalog_backend.persist_result({"scan_time":"2026-10-31T00:00:00Z"}, [])
except ImportError:
    pass
else:
    raise AssertionError("DB persistence must fail closed when its SDK is unavailable")
assert "db" not in sys.modules and "libsql_experimental" not in sys.modules''',
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
