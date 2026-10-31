"""First gateway cutover restores the trusted two-image control plane before recovery."""
from __future__ import annotations
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import pytest
CLOUD = Path(__file__).resolve().parents[1]
SCRIPT = CLOUD / 'scripts/model_gateway_rollback.py'

def load():
    spec = importlib.util.spec_from_file_location('gateway_rollback', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def fixture(tmp_path, old_helper='two-image-recover\n'):
    repo = tmp_path / 'repo'; repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
    git('init', '-q'); git('config', 'user.name', 'test'); git('config', 'user.email', 'test@example.test')
    (repo / 'cloud/scripts').mkdir(parents=True)
    source = repo / 'cloud/scripts/radon-app-runtime.sh'; source.write_text('two-image-runtime\n')
    (repo / 'cloud/scripts/deploy-root-helper.sh').write_text(old_helper)
    (repo / 'cloud/services/radon-.service.d').mkdir(parents=True)
    (repo / 'cloud/services/radon-.service.d/common.conf').write_text('old-provider-contract\n')
    git('add', '.'); git('commit', '-qm', 'old'); previous = git('rev-parse', 'HEAD')
    (repo / 'cloud/services/radon-model-gateway.service').write_text('gateway\n')
    source.write_text('three-image-runtime\n'); git('add', '.'); git('commit', '-qm', 'gateway'); target = git('rev-parse', 'HEAD')
    root = tmp_path / 'root'; root.mkdir()
    module = load()
    for rel, raw in {'/usr/local/sbin/radon-app-runtime': b'two-image-runtime\n', '/usr/local/sbin/radon-deploy-root':old_helper.encode(),'/etc/systemd/system/radon-.service.d/common.conf':b'old-provider-contract\n'}.items():
        dest = root / rel.lstrip('/'); dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(raw); dest.chmod(0o755 if 'sbin' in rel else 0o644)
    state = root / 'var/lib/radon'; state.mkdir(parents=True)
    import hashlib
    manifest = '\n'.join(f'{hashlib.sha256(raw).hexdigest()}  scripts/{name}.sh -> /usr/local/sbin/{targetname}' for name,targetname,raw in [('radon-app-runtime','radon-app-runtime',b'two-image-runtime\n'),('deploy-root-helper','radon-deploy-root',old_helper.encode())])+'\n'
    (state/'control-plane-manifest.sha256').write_text(manifest)
    (state/'control-plane-ready').write_text(hashlib.sha256(manifest.encode()).hexdigest()+'  /var/lib/radon/control-plane-manifest.sha256\n')
    return module,root,repo,previous,target

def test_two_image_release_restored_before_recover_without_lowering_floor(tmp_path):
    module,root,repo,previous,target = fixture(tmp_path)
    receipt = tmp_path / 'receipt'
    module.capture(root,repo,receipt,previous,target,owner_uid=os.getuid())
    runtime = root/'usr/local/sbin/radon-app-runtime'; runtime.write_text('three-image-runtime\n')
    newunit=root/'etc/systemd/system/radon-model-gateway.service';newunit.write_text('gateway\n')
    assert module.restore(root,repo,receipt,previous,target,owner_uid=os.getuid())
    assert runtime.read_text()=='two-image-runtime\n'
    assert (root/'usr/local/sbin/radon-deploy-root').read_text()=='two-image-recover\n'
    assert not newunit.exists()
    assert json.loads((receipt/'receipt.json').read_text())['target_sha']==target

@pytest.mark.parametrize('damage',['hash','symlink','unrelated_previous','wrong_floor'])
def test_refuses_modified_or_unrelated_rollback_receipt(tmp_path,damage):
    module,root,repo,previous,target=fixture(tmp_path); receipt=tmp_path/'receipt'
    module.capture(root,repo,receipt,previous,target,owner_uid=os.getuid())
    data=json.loads((receipt/'receipt.json').read_text()); record=next(r for r in data['files'] if r['target']=='/usr/local/sbin/radon-app-runtime')
    raw=receipt/record['blob']
    if damage=='hash': raw.chmod(0o600); raw.write_text('attacker')
    elif damage=='symlink': raw.unlink();raw.symlink_to(root/'usr/local/sbin/radon-app-runtime')
    if damage=='unrelated_previous': previous='f'*40
    if damage=='wrong_floor': target='e'*40
    with pytest.raises((ValueError,OSError,subprocess.CalledProcessError)):
        module.restore(root,repo,receipt,previous,target,owner_uid=os.getuid())
    assert (root/'usr/local/sbin/radon-app-runtime').read_text()=='two-image-runtime\n'

def test_cutover_capture_rejects_installed_bytes_absent_from_trusted_previous_commit(tmp_path):
    module,root,repo,previous,target=fixture(tmp_path)
    (root/'usr/local/sbin/radon-app-runtime').write_text('unverified root helper\n')
    with pytest.raises(ValueError): module.capture(root,repo,tmp_path/'receipt',previous,target,owner_uid=os.getuid())

def test_capture_precedes_first_bootstrap_overwrite_and_recover_executes_restored_helper():
    bootstrap=(CLOUD/'scripts/bootstrap-control-plane.sh').read_text()
    assert bootstrap.index('model_gateway_rollback.py" capture') < bootstrap.index('TRANSACTION_ACTIVE=1')
    helper=(CLOUD/'scripts/deploy-root-helper.sh').read_text().split('  recover)\n',1)[1].split('    ;;',1)[0]
    assert helper.index('model_gateway_rollback.py') < helper.index('install-model-clients')
    assert 'exec "$SELF" recover' in helper


def test_refuses_writable_destination_ancestor_before_any_restore_mutation(tmp_path):
    module,root,repo,previous,target=fixture(tmp_path); receipt=tmp_path/'receipt'
    module.capture(root,repo,receipt,previous,target,owner_uid=os.getuid())
    (root/'usr/local').chmod(0o777)
    with pytest.raises(ValueError):module.restore(root,repo,receipt,previous,target,owner_uid=os.getuid())
    assert (root/'usr/local/sbin/radon-app-runtime').read_text()=='two-image-runtime\n'


def test_actual_previous_helper_recovers_two_image_release_with_new_floor_preserved(tmp_path):
    from test_deploy_corrections import TestRootHelper, ROOT_HELPER
    # Exact deployed two-image predecessor, independent of checkout HEAD and
    # shallow CI history: 885f068aec9c1304892781f06e162c7178440c00.
    import hashlib
    raw=(CLOUD/'tests/fixtures/deploy-root-helper-885f068.sh.txt').read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='a9baecad2003e291371349bb065552a44aeb65b53b3c8dbe98267d264bbf3dc8'
    old_helper=raw.decode()
    module,root,repo,previous,target=fixture(tmp_path,old_helper)
    receipt=tmp_path/'receipt';module.capture(root,repo,receipt,previous,target,owner_uid=os.getuid())
    host=tmp_path/'host';host.mkdir()
    env,state,log,_,_=TestRootHelper()._root_helper_fixture(host)
    current=json.loads(state.read_text());current['units'].pop('radon-model-gateway.service');state.write_text(json.dumps(current))
    installed=root/'usr/local/sbin/radon-deploy-root'
    stopped=subprocess.run(['bash',str(installed),'stop-clean'],env=env,capture_output=True,text=True)
    assert stopped.returncode==0,stopped.stderr
    floor=tmp_path/'control-plane-floor';floor.write_text(target+'\n')
    module.restore(root,repo,receipt,previous,target,owner_uid=os.getuid())
    result=subprocess.run(['bash',str(installed),'recover'],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert floor.read_text()==target+'\n'
    assert 'radon-model-gateway.service' not in log.read_text()


def test_failed_bootstrap_retry_reuses_only_the_identical_verified_cutover_receipt(tmp_path):
    module,root,repo,previous,target=fixture(tmp_path);receipt=tmp_path/'receipt'
    module.capture(root,repo,receipt,previous,target,owner_uid=os.getuid())
    before=(receipt/'receipt.json').read_bytes()
    assert module.capture(root,repo,receipt,previous,target,owner_uid=os.getuid())
    assert (receipt/'receipt.json').read_bytes()==before
    (root/'usr/local/sbin/radon-app-runtime').write_text('unverified changed runtime\n')
    with pytest.raises(ValueError):module.capture(root,repo,receipt,previous,target,owner_uid=os.getuid())
