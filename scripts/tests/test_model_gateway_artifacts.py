"""Model weights use immutable proven revisions, never floating upstream main."""
import hashlib
import sys
from types import SimpleNamespace
import pytest


def test_chronos_load_uses_pinned_revision(monkeypatch):
    from model_gateway import _forecast_backend as backend
    seen=[]
    from model_gateway import artifacts
    monkeypatch.setattr(artifacts,'chronos_snapshot',lambda:'/pinned/snapshot')
    monkeypatch.setitem(sys.modules,'chronos',SimpleNamespace(Chronos2Pipeline=SimpleNamespace(from_pretrained=lambda *a,**k:seen.append((a,k)) or object())))
    monkeypatch.setattr(backend,'_PIPELINE_CACHE',{})
    backend._load_pipeline(backend.DEFAULT_MODEL_ID,'cpu')
    assert seen[0][1]['revision']=='29ec3766d36d6f73f0696f85560a422f50e8498c'


def test_embedding_snapshot_revision_and_digest(monkeypatch,tmp_path):
    from model_gateway import artifacts
    raw=b'known weights'; (tmp_path/'model_optimized.onnx').write_bytes(raw)
    seen=[]
    monkeypatch.setenv('FASTEMBED_CACHE_PATH',str(tmp_path/'cache'))
    record={**artifacts.MANIFEST['embedding'],'files':{'model_optimized.onnx':{'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)}}}
    monkeypatch.setattr(artifacts,'MANIFEST',{**artifacts.MANIFEST,'embedding':record})
    monkeypatch.setitem(sys.modules,'huggingface_hub',SimpleNamespace(snapshot_download=lambda **k:seen.append(k) or str(tmp_path)))
    assert artifacts.embedding_snapshot()==str(tmp_path)
    assert seen[0]['revision']=='52398278842ec682c6f32300af41344b1c0b0bb2'
    assert seen[0]['repo_id']=='qdrant/bge-small-en-v1.5-onnx-q'
    (tmp_path/'model_optimized.onnx').write_bytes(b'wrong weights')
    with pytest.raises(ValueError,match='digest'): artifacts.embedding_snapshot()
