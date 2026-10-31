"""First gateway cutover restores the trusted two-image control plane before recovery."""
from __future__ import annotations
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import pytest
CLOUD = Path(__file__).resolve().parents[1]
SCRIPT = CLOUD / 'scripts/model_gateway_rollback.py'

def load():
    spec = importlib.util.spec_from_file_location('gateway_rollback', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def fixture(tmp_path, old_helper='two-image-recover\n', *, old_keeper=False):
    repo = tmp_path / 'repo'; repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
    git('init', '-q'); git('config', 'user.name', 'test'); git('config', 'user.email', 'test@example.test')
    (repo / 'cloud/scripts').mkdir(parents=True)
    source = repo / 'cloud/scripts/radon-app-runtime.sh'; source.write_text('two-image-runtime\n')
    (repo / 'cloud/scripts/deploy-root-helper.sh').write_text(old_helper)
    (repo / 'cloud/services/radon-.service.d').mkdir(parents=True)
    (repo / 'cloud/services/radon-.service.d/common.conf').write_text('old-provider-contract\n')
    if old_keeper: (repo / 'cloud/services/radon-subscription-tokens.service').write_text('canonical old keeper\n')
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
    if old_keeper:
        unit = root / 'etc/systemd/system/radon-subscription-tokens.service'
        unit.write_text('canonical old keeper\n')
        unit.chmod(0o644)
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
    assert helper.index('model_gateway_rollback.py') < helper.index('recover-model-clients')
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


@pytest.mark.parametrize('independent_floor', [False, True])
@pytest.mark.parametrize('admission', ['valid', 'pin_changed', 'committed', 'malformed_floor', 'restore_failed'])
def test_restore_cli_reads_app_owned_head_as_application_uid_before_verified_restore(tmp_path, monkeypatch, independent_floor, admission):
    module, root, repo, previous, target = fixture(tmp_path)
    owner_uid = os.getuid()
    provision = tmp_path / 'provision'
    provision.mkdir()
    receipt = provision / 'model-gateway-cutover'
    module.capture(root, repo, receipt, previous, target, owner_uid=owner_uid)
    repo.rename(provision / 'radon.git')
    subprocess.run(['git', '-C', str(provision / 'radon.git'), 'update-ref', 'refs/remotes/pinned/main', previous if admission == 'pin_changed' else target], check=True)
    floor_value = 'f' * 40 if independent_floor else target
    if admission == 'malformed_floor': floor_value = 'malformed'
    (provision / 'control-plane-floor').write_text(floor_value + '\n')
    committed = tmp_path / 'committed-state'
    committed.mkdir()
    if admission == 'committed': (committed / 'committed-release').write_text(target + '\n')
    monkeypatch.setattr(module, 'COMMITTED_STATE', committed, raising=False)
    (root / 'usr/local/sbin/radon-app-runtime').write_text('failed gateway runtime\n')
    monkeypatch.setattr(module, 'PROVISION', provision)
    monkeypatch.setattr(module, 'RECEIPT', receipt)
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    original_dir, original_regular, original_restore = module.trusted_dir, module.regular, module.restore
    monkeypatch.setattr(module, 'trusted_dir', lambda path, uid: original_dir(path, owner_uid))
    monkeypatch.setattr(module, 'regular', lambda path, uid: original_regular(path, owner_uid))
    original_output = subprocess.check_output
    observed = []

    def output(command, **kwargs):
        if '/home/radon/radon' not in command:
            return original_output(command, **kwargs)
        observed.append(command)
        # Git refuses root's read of this application-owned checkout. The
        # production metadata lookup must lower privilege instead of relaxing
        # safe.directory or reading operator Git configuration.
        if command[:5] != ['/usr/bin/setpriv', '--reuid=1000', '--regid=1000', '--clear-groups', '/usr/bin/git']:
            raise subprocess.CalledProcessError(128, command, stderr=b'dubious ownership')
        assert command[5:] == ['-C', '/home/radon/radon', 'rev-parse', 'HEAD']
        assert kwargs['env'] == module.GIT_ENV
        return (previous + '\n').encode()

    monkeypatch.setattr(module.subprocess, 'check_output', output)

    def restore(host_root, trusted_repo, trusted_receipt, actual_previous, floor):
        assert host_root == Path('/')
        assert (actual_previous, floor) == (previous, target)
        if admission == 'restore_failed': raise ValueError('fixture restoration failure')
        with monkeypatch.context() as context:
            context.setattr(module.os, 'geteuid', lambda: owner_uid)
            return original_restore(root, trusted_repo, trusted_receipt, actual_previous, floor, owner_uid=owner_uid)

    monkeypatch.setattr(module, 'restore', restore)
    expected = 0 if admission == 'valid' else 78 if admission in {'malformed_floor', 'restore_failed'} else 3
    assert module.main(['restore']) == expected
    if admission == 'valid': assert len(observed) == 1
    assert (root / 'usr/local/sbin/radon-app-runtime').read_text() == ('two-image-runtime\n' if admission == 'valid' else 'failed gateway runtime\n')
    assert (provision / 'control-plane-floor').read_text() == floor_value + '\n'
    assert receipt.exists()
    assert not list(provision.glob('model-gateway-cutover.restored.*'))


def test_restore_hardens_only_known_sticky_config_parent_and_restores_its_mode(tmp_path, monkeypatch):
    module, root, repo, previous, target = fixture(tmp_path)
    receipt = tmp_path / 'receipt'
    module.capture(root, repo, receipt, previous, target, owner_uid=os.getuid())
    folder = root / 'etc/radon'
    folder.mkdir(parents=True)
    folder.chmod(0o1770)
    before = folder.stat()
    original_atomic = module._atomic
    def atomic(*args):
        assert folder.stat().st_mode & 0o7777 == 0o1750
        return original_atomic(*args)
    monkeypatch.setattr(module, '_atomic', atomic)
    assert module.restore(root, repo, receipt, previous, target, owner_uid=os.getuid())
    after = folder.stat()
    assert (after.st_dev, after.st_ino, after.st_uid, after.st_gid) == (before.st_dev, before.st_ino, before.st_uid, before.st_gid)
    assert after.st_mode & 0o7777 == 0o1770


@pytest.mark.parametrize('mode', [0o770, 0o1772, 0o1777])
def test_restore_does_not_admit_other_group_or_world_writable_config_modes(tmp_path, mode):
    module, root, repo, previous, target = fixture(tmp_path)
    receipt = tmp_path / 'receipt'
    module.capture(root, repo, receipt, previous, target, owner_uid=os.getuid())
    folder = root / 'etc/radon'
    folder.mkdir(parents=True)
    folder.chmod(mode)
    with pytest.raises(ValueError):
        module.restore(root, repo, receipt, previous, target, owner_uid=os.getuid())
    assert folder.stat().st_mode & 0o7777 == mode


def test_config_parent_permissions_are_restored_when_another_ancestor_is_unsafe(tmp_path):
    module, root, repo, previous, target = fixture(tmp_path)
    receipt = tmp_path / 'receipt'
    module.capture(root, repo, receipt, previous, target, owner_uid=os.getuid())
    folder = root / 'etc/radon'
    folder.mkdir(parents=True)
    folder.chmod(0o1770)
    (root / 'usr/local').chmod(0o777)
    with pytest.raises(ValueError):
        module.restore(root, repo, receipt, previous, target, owner_uid=os.getuid())
    assert folder.stat().st_mode & 0o7777 == 0o1770


def test_config_parent_permissions_are_restored_if_artifact_replacement_fails(tmp_path, monkeypatch):
    module, root, repo, previous, target = fixture(tmp_path)
    receipt = tmp_path / 'receipt'
    module.capture(root, repo, receipt, previous, target, owner_uid=os.getuid())
    folder = root / 'etc/radon'
    folder.mkdir(parents=True)
    folder.chmod(0o1770)
    def fail(*args):
        assert folder.stat().st_mode & 0o7777 == 0o1750
        raise OSError('fixture write failure')
    monkeypatch.setattr(module, '_atomic', fail)
    with pytest.raises(OSError):
        module.restore(root, repo, receipt, previous, target, owner_uid=os.getuid())
    assert folder.stat().st_mode & 0o7777 == 0o1770
    assert (receipt / 'receipt.json').is_file()


def test_completed_restore_archives_audit_receipt_without_blocking_a_new_cutover(tmp_path):
    module, root, repo, previous, target = fixture(tmp_path)
    provision = tmp_path / 'provision'
    provision.mkdir()
    receipt = provision / 'model-gateway-cutover'
    module.capture(root, repo, receipt, previous, target, owner_uid=os.getuid())
    raw = (receipt / 'receipt.json').read_bytes()
    module.restore(root, repo, receipt, previous, target, owner_uid=os.getuid())
    assert (receipt / 'receipt.json').read_bytes() == raw
    source = repo / 'cloud/services/radon-model-gateway.service'
    source.write_text('repaired gateway\n')
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'repair'], check=True)
    repaired = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    module.capture(root, repo, receipt, previous, repaired, owner_uid=os.getuid())
    archives = list(provision.glob('model-gateway-cutover.restored.' + target + '.*'))
    assert len(archives) == 1
    archive = archives[0]
    assert json.loads((receipt / 'receipt.json').read_text())['target_sha'] == repaired
    assert (archive / 'receipt.json').read_bytes() == raw


def test_capture_preserves_trusted_old_keeper_missing_from_legacy_manifest(tmp_path):
    module, root, repo, previous, target = fixture(tmp_path, old_keeper=True)
    receipt = tmp_path / 'receipt'
    module.capture(root, repo, receipt, previous, target, owner_uid=os.getuid())
    row = next(row for row in json.loads((receipt / 'receipt.json').read_text())['files'] if row['target'] == '/etc/systemd/system/radon-subscription-tokens.service')
    assert row['blob'] is not None
    unit = root / 'etc/systemd/system/radon-subscription-tokens.service'
    unit.write_text('gateway refresh client\n')
    module.restore(root, repo, receipt, previous, target, owner_uid=os.getuid())
    assert unit.read_text() == 'canonical old keeper\n'


@pytest.mark.parametrize('damage', ['bytes', 'mode', 'symlink'])
def test_capture_refuses_untrusted_old_unmanifested_keeper(tmp_path, damage):
    module, root, repo, previous, target = fixture(tmp_path, old_keeper=True)
    unit = root / 'etc/systemd/system/radon-subscription-tokens.service'
    if damage == 'bytes': unit.write_text('untrusted keeper\n')
    elif damage == 'mode': unit.chmod(0o600)
    else:
        unit.unlink()
        unit.symlink_to(root / 'usr/local/sbin/radon-app-runtime')
    with pytest.raises(ValueError):
        module.capture(root, repo, tmp_path / 'receipt', previous, target, owner_uid=os.getuid())
    assert not (tmp_path / 'receipt').exists()


def test_capture_preserves_true_absence_even_when_previous_git_has_a_keeper(tmp_path):
    module, root, repo, previous, target = fixture(tmp_path, old_keeper=True)
    unit = root / 'etc/systemd/system/radon-subscription-tokens.service'
    unit.unlink()
    receipt = tmp_path / 'receipt'
    module.capture(root, repo, receipt, previous, target, owner_uid=os.getuid())
    module.restore(root, repo, receipt, previous, target, owner_uid=os.getuid())
    assert not unit.exists()


@pytest.mark.parametrize('child_umask', [0o077, 0o777])
def test_sigkill_during_restore_retains_durable_config_mode_then_retry_restores_it(tmp_path, child_umask):
    module, root, repo, previous, target = fixture(tmp_path)
    receipt = tmp_path / 'receipt'
    module.capture(root, repo, receipt, previous, target, owner_uid=os.getuid())
    folder = root / 'etc/radon'
    folder.mkdir(parents=True)
    folder.chmod(0o1770)
    ready = tmp_path / 'ready'
    child = """
import importlib.util, os, pathlib, sys, time
spec=importlib.util.spec_from_file_location('rollback_child',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
os.umask(int(sys.argv[8]))
def pause(*args):
    pathlib.Path(sys.argv[7]).write_text('sealed')
    time.sleep(60)
module._atomic=pause
module.restore(*sys.argv[2:7],owner_uid=os.getuid())
"""
    process = subprocess.Popen([sys.executable, '-I', '-c', child, str(SCRIPT), str(root), str(repo), str(receipt), previous, target, str(ready), str(child_umask)])
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), 'restore did not reach its first artifact replacement'
        assert folder.stat().st_mode & 0o7777 == 0o1750
        process.kill()
        process.wait(timeout=5)
        marker = receipt / 'config-parent-mode.json'
        assert marker.is_file()
        assert marker.stat().st_mode & 0o777 == 0o600
        retry = """
import importlib.util, os, sys
spec=importlib.util.spec_from_file_location('rollback_retry',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.restore(*sys.argv[2:7],owner_uid=os.getuid())
"""
        subprocess.run([sys.executable, '-I', '-c', retry, str(SCRIPT), str(root), str(repo), str(receipt), previous, target], check=True, timeout=10)
        assert folder.stat().st_mode & 0o7777 == 0o1770
        assert not marker.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_legitimate_sealed_config_mode_without_receipt_marker_is_not_widened(tmp_path):
    module, root, repo, previous, target = fixture(tmp_path)
    receipt = tmp_path / 'receipt'
    module.capture(root, repo, receipt, previous, target, owner_uid=os.getuid())
    folder = root / 'etc/radon'
    folder.mkdir(parents=True)
    folder.chmod(0o1750)
    module.restore(root, repo, receipt, previous, target, owner_uid=os.getuid())
    assert folder.stat().st_mode & 0o7777 == 0o1750


@pytest.mark.parametrize('damage', ['malformed', 'null', 'inode', 'prior_mode', 'symlink', 'owner', 'permissions'])
def test_mode_resume_refuses_unsafe_or_unrelated_durable_marker(tmp_path, monkeypatch, damage):
    module, root, repo, previous, target = fixture(tmp_path)
    receipt = tmp_path / 'receipt'
    module.capture(root, repo, receipt, previous, target, owner_uid=os.getuid())
    folder = root / 'etc/radon'
    folder.mkdir(parents=True)
    folder.chmod(0o1750)
    info = folder.stat()
    record = {'version': 1, 'path': '/etc/radon', 'dev': info.st_dev, 'ino': info.st_ino,
              'uid': os.getuid(), 'gid': os.getgid(), 'prior_mode': 0o1770, 'sealed_mode': 0o1750}
    if damage == 'inode': record['ino'] += 1
    if damage == 'prior_mode': record['prior_mode'] = 0o1777
    marker = receipt / 'config-parent-mode.json'
    marker.write_text('{invalid' if damage == 'malformed' else 'null' if damage == 'null' else json.dumps(record))
    marker.chmod(0o644 if damage == 'permissions' else 0o600)
    if damage == 'symlink':
        outside = tmp_path / 'outside'
        marker.rename(outside)
        marker.symlink_to(outside)
    if damage == 'owner':
        marker_info = marker.stat()
        original = module.os.fstat
        def wrong_owner(fd):
            result = original(fd)
            if (result.st_dev, result.st_ino) == (marker_info.st_dev, marker_info.st_ino):
                fields = list(result)
                fields[4] += 1
                return os.stat_result(fields)
            return result
        monkeypatch.setattr(module.os, 'fstat', wrong_owner)
    with pytest.raises((ValueError, OSError)):
        module.restore(root, repo, receipt, previous, target, owner_uid=os.getuid())
    assert folder.stat().st_mode & 0o7777 == 0o1750
    assert marker.exists()


@pytest.mark.parametrize('marker_kind', ['valid', 'malformed', 'symlink'])
def test_capture_never_reuses_or_archives_pending_permission_recovery(tmp_path, marker_kind):
    module, root, repo, previous, target = fixture(tmp_path)
    receipt = tmp_path / 'receipt'
    module.capture(root, repo, receipt, previous, target, owner_uid=os.getuid())
    raw = (receipt / 'receipt.json').read_bytes()
    marker = receipt / 'config-parent-mode.json'
    marker.write_text('{}' if marker_kind == 'valid' else '{invalid')
    marker.chmod(0o600)
    if marker_kind == 'symlink':
        outside = tmp_path / 'outside'
        marker.rename(outside)
        marker.symlink_to(outside)
    source = repo / 'cloud/services/radon-model-gateway.service'
    source.write_text('next gateway attempt\n')
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'next attempt'], check=True)
    following = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    with pytest.raises(ValueError):
        module.capture(root, repo, receipt, previous, following, owner_uid=os.getuid())
    assert (receipt / 'receipt.json').read_bytes() == raw
    assert marker.exists()
    assert not list(tmp_path.glob('receipt.restored.*'))


@pytest.mark.parametrize('gateway_exists', [False, True])
def test_capture_cli_refuses_pending_mode_intent_before_either_fast_exit(tmp_path, monkeypatch, gateway_exists):
    module = load()
    owner_uid = os.getuid()
    provision = tmp_path / 'provision'
    (provision / 'radon.git').mkdir(parents=True)
    receipt = provision / 'model-gateway-cutover'
    receipt.mkdir(mode=0o700)
    marker = receipt / 'config-parent-mode.json'
    marker.write_text('{}')
    marker.chmod(0o600)
    monkeypatch.setattr(module, 'PROVISION', provision)
    monkeypatch.setattr(module, 'RECEIPT', receipt)
    monkeypatch.setattr(module, 'MANIFEST', str(tmp_path / 'no-manifest'))
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    original_dir = module.trusted_dir
    monkeypatch.setattr(module, 'trusted_dir', lambda path, uid: original_dir(path, owner_uid))
    original_exists = Path.exists
    monkeypatch.setattr(Path, 'exists', lambda path: gateway_exists if str(path) == '/etc/systemd/system/radon-model-gateway.service' else original_exists(path))
    assert module.main(['capture']) == 78
    assert marker.is_file()


def test_restore_helper_reloads_restored_units_before_execing_previous_helper():
    helper = (CLOUD / 'scripts/deploy-root-helper.sh').read_text().split('  recover)\n', 1)[1].split('    ;;', 1)[0]
    assert helper.index('systemctl_bounded daemon-reload') < helper.index('exec "$SELF" recover')
