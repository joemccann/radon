"""Only pinned model assets cross from legacy caches into the gateway cache."""
import hashlib
import importlib.util
from pathlib import Path
import pytest
CLOUD=Path(__file__).resolve().parents[1]
SCRIPT=CLOUD.parent/'scripts/model_gateway/cache_migrate.py'

def module():
    spec=importlib.util.spec_from_file_location('model_cache',SCRIPT)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

def source(tmp_path):
    root=tmp_path/'model';snapshot=root/'snapshots'/('a'*40);snapshot.mkdir(parents=True)
    (root/'blobs').mkdir();(root/'blobs/pinned').write_bytes(b'verified weights')
    (snapshot/'model.onnx').symlink_to('../../blobs/pinned')
    (root/'token').write_text('must not cross')
    files={'model.onnx':{'sha256':hashlib.sha256(b'verified weights').hexdigest(),'size':16}}
    return root,files

def test_copies_only_verified_snapshot_assets_and_records_hash_proof(tmp_path):
    root,files=source(tmp_path);dest=tmp_path/'new'
    proof=module().migrate(root,dest,'a'*40,files)
    assert (dest/'snapshots'/('a'*40)/'model.onnx').read_bytes()==b'verified weights'
    assert not list(dest.rglob('token'))
    assert proof['files']==files
    assert not (dest/'snapshots'/('a'*40)/'model.onnx').is_symlink()

@pytest.mark.parametrize('damage',['hash','size','escape','destination_link'])
def test_refuses_tampering_and_escaping_sources_without_promoting_snapshot(tmp_path,damage):
    migration=module()
    root,files=source(tmp_path);dest=tmp_path/'new'
    if damage=='hash':files['model.onnx']['sha256']='f'*64
    elif damage=='size':files['model.onnx']['size']=1
    elif damage=='escape':
        (tmp_path/'outside').write_bytes(b'verified weights');p=root/'snapshots'/('a'*40)/'model.onnx';p.unlink();p.symlink_to(tmp_path/'outside')
    else:
        other=tmp_path/'other';other.mkdir();dest.symlink_to(other)
    with pytest.raises((ValueError,OSError)):migration.migrate(root,dest,'a'*40,files)
    assert not (dest/'snapshots'/('a'*40)).exists()

def test_cache_migration_is_image_owned_unprivileged_and_no_provider_home_mount():
    runtime=(CLOUD/'scripts/radon-app-runtime.sh').read_text()
    body=runtime.split('migrate_model_cache() {',1)[1].split('\n}',1)[0]
    assert '--user "$ids"' in body and '--network none' in body
    assert 'cache_migrate.py' in body and '--pull=never' in body
    assert '.cache/huggingface/hub/models--amazon--chronos-2' in body
    assert '.cache/huggingface:' not in body
