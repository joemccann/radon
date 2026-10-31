"""Model preparation must fit without consuming root's filesystem reserve."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from test_model_clients_install import module

CLOUD = Path(__file__).resolve().parents[1]
DEPLOY = CLOUD / 'scripts/deploy.sh'


def setup_volumes(monkeypatch, *, devices=(1, 1, 1, 1), available=100 * 1024**3):
    api = module()
    paths = (api.ROOT, api.EXPORT_ROOT, api.MODEL_CACHE_ROOT, api.RECOVERY_ROOT)
    monkeypatch.setattr(api, '_space_probe', lambda path: (devices[paths.index(path)], 4096, available, 1000000))
    return api


def test_reserved_root_blocks_are_not_available_to_model_uid(tmp_path, monkeypatch):
    api = module()
    monkeypatch.setattr(api.os, 'fstatvfs', lambda fd: SimpleNamespace(f_bavail=0, f_bfree=500_000, f_frsize=4096, f_bsize=4096,f_files=1000000,f_favail=1000000))
    device, block, available, inodes = api._space_probe(tmp_path / 'not-created')
    assert device == tmp_path.stat().st_dev and block == 4096
    assert available == 0
    assert not (tmp_path / 'not-created').exists()


def linux_tree(api, block=4096):
    return api.LINUX_MAX_PAYLOAD_BYTES + (2 * api.LINUX_MAX_MEMBERS + 32) * block


def linux_stream(api, block=4096):
    return -(-api.LINUX_MAX_STREAM_BYTES // block) * block + 32 * block


def darwin_stage(api, block=4096):
    return 2 * -(-api.DARWIN_MAX_EXPORT_BYTES // block) * block + 32 * block


def absent_model_bytes(api, block=4096):
    return sum((size + block - 1) // block * block for size in api.PINNED_MODEL_FILE_BYTES)


MODEL_OVERHEAD = 64 * 4096 + 2 * 65536


def test_same_filesystem_uses_maximum_sequential_peak_not_sum(monkeypatch):
    api = setup_volumes(monkeypatch)
    monkeypatch.setattr(api, '_model_file_present', lambda *args: False)
    proof = api.check_model_space()
    peak = max(linux_stream(api) + linux_tree(api), linux_tree(api) + darwin_stage(api))
    assert proof['filesystems'] == [{'device': 1, 'available_bytes': 100 * 1024**3, 'required_bytes': peak + absent_model_bytes(api) + MODEL_OVERHEAD + api.RUNTIME_RESERVE_BYTES, 'available_inodes':1000000, 'required_inodes':2*api.LINUX_MAX_MEMBERS+32+32+64+len(api.PINNED_MODEL_FILE_BYTES)+2+api.RUNTIME_RESERVE_INODES}]


def test_present_verified_model_cache_is_not_charged_again(monkeypatch):
    api = setup_volumes(monkeypatch)
    monkeypatch.setattr(api, '_model_file_present', lambda *args: True)
    required = api.check_model_space()['filesystems'][0]['required_bytes']
    peak = max(linux_stream(api) + linux_tree(api), linux_tree(api) + darwin_stage(api))
    assert required == peak + MODEL_OVERHEAD + api.RUNTIME_RESERVE_BYTES


def test_production_shaped_volume_admits_with_cache_present_and_old_bound_did_not(monkeypatch):
    # radon-app 2026-10-31: 6721466368 bytes available to UID1000 on a shared
    # 4KiB ext4 root; the generic 2GiB/100000-member bound demanded 8881176576.
    api = setup_volumes(monkeypatch, available=6_721_466_368)
    monkeypatch.setattr(api, '_model_file_present', lambda *args: True)
    row = api.check_model_space()['filesystems'][0]
    assert row['required_bytes'] == 5_144_893_312
    assert row['required_bytes'] < 6_721_466_368 < 8_881_176_576


def test_model_cache_credit_requires_exact_size_and_digest(tmp_path, monkeypatch):
    import hashlib
    api = module()
    good = b'pinned weights'
    (tmp_path / 'hub').mkdir()
    (tmp_path / 'hub/model.bin').write_bytes(good)
    monkeypatch.setattr(api, 'MODEL_CACHE_ROOT', tmp_path)
    digest = hashlib.sha256(good).hexdigest()
    assert api._model_file_present('hub/model.bin', len(good), digest)
    assert not api._model_file_present('hub/model.bin', len(good) + 1, digest)
    assert not api._model_file_present('hub/model.bin', len(good), '0' * 64)
    assert not api._model_file_present('hub/missing.bin', len(good), digest)
    (tmp_path / 'hub/link.bin').symlink_to(tmp_path / 'hub/model.bin')
    assert not api._model_file_present('hub/link.bin', len(good), digest)


def test_separate_filesystems_each_reserve_their_own_peak(monkeypatch):
    api = setup_volumes(monkeypatch, devices=(1, 2, 3, 4))
    monkeypatch.setattr(api, '_model_file_present', lambda *args: False)
    rows = {row['device']: row for row in api.check_model_space()['filesystems']}
    assert rows[1]['required_bytes'] == linux_stream(api) + linux_tree(api)
    assert rows[2]['required_bytes'] == darwin_stage(api)
    assert rows[3]['required_bytes'] > sum(api.PINNED_MODEL_FILE_BYTES)
    assert rows[4]['required_bytes'] == api.RUNTIME_RESERVE_BYTES == 1024**3


def test_admission_budget_is_derived_from_the_enforced_bounds(monkeypatch):
    api = setup_volumes(monkeypatch, devices=(1, 2, 3, 4))
    before = {row['device']: row['required_bytes'] for row in api.check_model_space()['filesystems']}
    monkeypatch.setattr(api, 'LINUX_MAX_PAYLOAD_BYTES', api.LINUX_MAX_PAYLOAD_BYTES + 4096)
    monkeypatch.setattr(api, 'LINUX_MAX_STREAM_BYTES', api.LINUX_MAX_STREAM_BYTES + 4096)
    monkeypatch.setattr(api, 'DARWIN_MAX_EXPORT_BYTES', api.DARWIN_MAX_EXPORT_BYTES + 4096)
    after = {row['device']: row['required_bytes'] for row in api.check_model_space()['filesystems']}
    assert after[1] - before[1] == 2 * 4096
    assert after[2] - before[2] == 2 * 4096


def test_pinned_bounds_keep_headroom_over_observed_production_release():
    api = module()
    # Observed on radon-app for 38154af: Linux payload/members/stream and the
    # Darwin bundle.tar. Bounds stay within ~20-30% so admission is not generic.
    linux_payload, linux_members, linux_stream_bytes, darwin = 1_621_817_729, 148, 1_621_964_800, 700_866_560
    for observed, bound in ((linux_payload, api.LINUX_MAX_PAYLOAD_BYTES), (linux_stream_bytes, api.LINUX_MAX_STREAM_BYTES), (darwin, api.DARWIN_MAX_EXPORT_BYTES)):
        assert 1.20 * observed <= bound <= 1.30 * observed
    assert linux_members * 4 <= api.LINUX_MAX_MEMBERS <= 2048
    assert api.LINUX_MAX_STREAM_BYTES == api.LINUX_MAX_PAYLOAD_BYTES + api.LINUX_MAX_MEMBERS * 2048 + 10240
    assert not hasattr(api, 'MAX_BYTES') and not hasattr(api, 'MAX_MEMBERS')


def test_any_low_space_volume_refuses_even_with_root_reserve(monkeypatch):
    api = setup_volumes(monkeypatch, devices=(1, 2, 3, 4))
    original = api._space_probe
    monkeypatch.setattr(api, '_space_probe', lambda path: (3, 4096, 0, 1000000) if path == api.MODEL_CACHE_ROOT else original(path))
    with pytest.raises(OSError) as error:
        api.check_model_space()
    assert error.value.errno == errno.ENOSPC


@pytest.mark.parametrize('value', [None, PermissionError('private path'), OSError(errno.EIO, 'bad filesystem')])
def test_unknown_filesystem_metadata_fails_closed(monkeypatch, value):
    api = module()
    def probe(path):
        if isinstance(value, BaseException): raise value
        return value
    monkeypatch.setattr(api, '_space_probe', probe)
    monkeypatch.setattr(api.os, 'geteuid', lambda: 0)
    assert api.main(['check-space']) == 78


def test_space_recheck_precedes_all_promotion_writes(monkeypatch):
    api = module()
    calls = []
    monkeypatch.setattr(api.os, 'geteuid', lambda: 0)
    def refuse():
        calls.append('admission')
        raise OSError(errno.ENOSPC, 'fixture-only secret path must not escape')
    monkeypatch.setattr(api, 'check_model_space', refuse)
    monkeypatch.setattr(api, '_trusted_root', lambda *args: calls.append('mkdir'))
    monkeypatch.setattr(api, 'export_from_image', lambda *args, **kwargs: calls.append('export'))
    assert api.main(['/usr/bin/podman', 'ghcr.io/joemccann/radon-model-gateway:' + 'a' * 40, 'a' * 40]) == 78
    assert calls == ['admission']


def test_fixed_cache_budget_matches_image_pinned_manifest():
    api = module()
    manifest = json.loads((CLOUD.parent / 'scripts/model_gateway/model-artifacts.json').read_text())
    assert sorted(api.PINNED_MODEL_FILE_BYTES) == sorted(entry['size'] for name in ('chronos', 'embedding') for entry in manifest[name]['files'].values())
    # HF_HOME=<cache>/huggingface and FASTEMBED_CACHE_PATH=<cache>/fastembed.
    expected = set()
    for kind, base in (('chronos', 'huggingface/hub'), ('embedding', 'fastembed')):
        record = manifest[kind]
        folder = base + '/models--' + record['repo'].replace('/', '--') + '/snapshots/' + record['revision']
        expected |= {(folder + '/' + name, entry['size'], entry['sha256']) for name, entry in record['files'].items()}
    assert set(api.PINNED_MODEL_FILES) == expected


@pytest.mark.parametrize('args', [['check-space', '/tmp/override'], ['check-space', '0']])
def test_space_cli_has_no_caller_paths_or_budget_override(monkeypatch, args):
    api = module()
    monkeypatch.setattr(api.os, 'geteuid', lambda: 0)
    assert api.main(args) == 64


def test_successful_pull_then_space_failure_keeps_consumers_running(tmp_path):
    calls = tmp_path / 'calls'
    shell = f'''set -euo pipefail
source {DEPLOY}
sudo() {{ printf '%s\\n' "$*" >> {calls}; return 0; }}
timeout() {{ shift; "$@"; }}
preflight_model_disk() {{ printf 'disk-refused\\n' >> {calls}; return 78; }}
prepull_app_images {'a'*40}
printf 'teardown\\n' >> {calls}
'''
    result = subprocess.run(['bash', '-c', shell], capture_output=True, text=True)
    assert result.returncode != 0
    lines = calls.read_text().splitlines()
    assert any(' pull ' in line for line in lines)
    assert lines[-1] == 'disk-refused'
    assert 'teardown' not in lines


def test_space_loss_after_prestage_is_rechecked_before_teardown(tmp_path):
    calls = tmp_path / 'calls'
    shell = f'''set -euo pipefail
source {DEPLOY}
wait_for_gateway_ready() {{ return 0; }}
preflight_model_disk() {{ printf 'disk-refused\\n' >> {calls}; return 78; }}
sudo() {{ printf 'unsafe-stop\\n' >> {calls}; }}
stop_services_for_transition
'''
    result = subprocess.run(['bash', '-c', shell], capture_output=True, text=True)
    assert result.returncode != 0
    assert calls.read_text().splitlines() == ['disk-refused']


def test_root_runtime_admission_is_fixed_readonly_and_sudoers_has_no_wildcard():
    runtime = (CLOUD / 'scripts/radon-app-runtime.sh').read_text()
    assert 'check-model-space)\n    [[ $# -eq 1 ]] || usage' in runtime
    assert '"$PYTHON" -I /usr/local/lib/radon/model_clients.py check-space' in runtime
    sudoers = (CLOUD / 'config/sudoers.d/radon-deploy').read_text()
    assert '/usr/local/sbin/radon-app-runtime check-model-space' in sudoers
    assert 'check-model-space *' not in sudoers
    # Recovery must not be prevented by this deployment-only capacity policy.
    helper = (CLOUD / 'scripts/deploy-root-helper.sh').read_text()
    recover = helper.split('  recover)\n', 1)[1].split('    ;;', 1)[0]
    assert 'check-model-space' not in recover


def test_prestage_admission_failure_creates_no_worktree_or_build(tmp_path):
    calls=tmp_path/'calls'
    shell=f'''set -euo pipefail
source {DEPLOY}
preflight_model_disk() {{ printf 'disk-refused\\n' >> {calls}; return 78; }}
create_staged_worktree() {{ printf 'unsafe-worktree\\n' >> {calls}; }}
build_nextjs_at() {{ printf 'unsafe-build\\n' >> {calls}; }}
prepare_python_wheels() {{ printf 'unsafe-wheels\\n' >> {calls}; }}
build_staged_release {'a'*40}
'''
    result=subprocess.run(['bash','-c',shell],capture_output=True,text=True)
    assert result.returncode==78
    assert calls.read_text().splitlines()==['disk-refused']


def test_disk_refusal_never_becomes_fallback_host_compile(tmp_path):
    staged=tmp_path/'staged';(staged/'web').mkdir(parents=True)
    for path in ('package.json','bun.lock','web/package.json','web/bun.lock'):(staged/path).write_text('{}')
    calls=tmp_path/'calls'
    shell=f'''set -euo pipefail
source {DEPLOY}
write_web_env() {{ return 0; }}
bun() {{ if [[ "$1" == --version ]]; then printf '%s\\n' "$BUN_VERSION"; else printf 'unsafe-install\\n' >> {calls}; fi; }}
seed_staged_node_modules() {{ SEEDED_ROOT_NODE_MODULES=1; SEEDED_WEB_NODE_MODULES=1; }}
should_rebuild_next() {{ return 0; }}
container_node_image_replaces_next_compile() {{ return 78; }}
run_with_cloud_env() {{ printf 'unsafe-build\\n' >> {calls}; }}
build_nextjs_at {staged}
'''
    result=subprocess.run(['bash','-c',shell],capture_output=True,text=True,env={**os.environ,'PLAYWRIGHT_BROWSERS_PATH':str(staged)})
    assert result.returncode==78
    assert not calls.exists()


def test_inode_exhaustion_refuses_even_with_abundant_writable_bytes(tmp_path,monkeypatch):
    api=module()
    for name in ('ROOT','EXPORT_ROOT','MODEL_CACHE_ROOT','RECOVERY_ROOT'):monkeypatch.setattr(api,name,tmp_path)
    monkeypatch.setattr(api.os,'fstatvfs',lambda fd:SimpleNamespace(f_bavail=100*1024**3//4096,f_frsize=4096,f_bsize=4096,f_files=1000000,f_favail=0))
    with pytest.raises(OSError) as error: api.check_model_space()
    assert error.value.errno==errno.ENOSPC


@pytest.mark.parametrize(('files','available'),[(0,0),(0,-1),(1000,-1)])
def test_unknown_or_unbounded_inode_metadata_is_not_assumed_usable(tmp_path,monkeypatch,files,available):
    api=module()
    monkeypatch.setattr(api.os,'fstatvfs',lambda fd:SimpleNamespace(f_bavail=100000000,f_frsize=4096,f_bsize=4096,f_files=files,f_favail=available))
    with pytest.raises(ValueError):api._space_probe(tmp_path)


def test_inode_peak_counts_members_and_metadata_per_device(monkeypatch):
    api=module()
    monkeypatch.setattr(api,'_space_probe',lambda path:(1,4096,100*1024**3,1000000))
    row=api.check_model_space()['filesystems'][0]
    assert row['available_inodes']==1000000
    assert row['required_inodes']==2*api.LINUX_MAX_MEMBERS+32+32+64+len(api.PINNED_MODEL_FILE_BYTES)+2+api.RUNTIME_RESERVE_INODES


@pytest.mark.parametrize('mode',['exit-trap','conditional'])
def test_final_admission_refusal_creates_no_journal_or_recovery_teardown(tmp_path,mode):
    import sys
    releases=tmp_path/'releases';stage=releases/'stage.target';stage.mkdir(parents=True)
    live=tmp_path/'live';live.mkdir();health=live/'serving';health.write_text('healthy-old-release')
    journal=tmp_path/'journal.json';calls=tmp_path/'calls'
    body='run_deploy_internal target' if mode=='exit-trap' else "restart_services '"+'a'*40+"' '"+'b'*40+"' || exit $?"
    shell=f'''set -euo pipefail
source {DEPLOY}
STAGED_RELEASE_DIR={stage}
validate_release_artifacts() {{ return 0; }}
validate_rollback_artifacts() {{ return 0; }}
wait_for_gateway_ready() {{ return 0; }}
payload_paths_changed() {{ return 0; }}
current_commit() {{ printf '%s\\n' {'b'*40}; }}
preflight_model_disk() {{ return 78; }}
cleanup_staged_release() {{ return 0; }}
sudo() {{ printf '%s\\n' "$*" >> {calls}; printf stopped > {health}; return 1; }}
activate_staged_release() {{ printf 'unsafe-activation\\n' >> {calls}; }}
main() {{ restart_services {'a'*40} {'b'*40}; }}
set +e
( set -e; {body} )
status=$?
set -e
# The actual outer recovery path must also see no pending transition.
supervisor_recover_once
exit "$status"
'''
    result=subprocess.run(['bash','-c',shell],capture_output=True,text=True,env={**os.environ,'RADON_DIR':str(live),'RADON_RELEASES_DIR':str(releases),'RADON_DEPLOY_TRANSITION_JOURNAL':str(journal),'RADON_DEPLOY_JOURNAL_PYTHON':sys.executable})
    assert result.returncode==78,result.stdout+result.stderr
    assert health.read_text()=='healthy-old-release'
    assert not calls.exists(),result.stdout+result.stderr
    assert not journal.exists()
    assert list(releases.iterdir())==[stage]


@pytest.mark.parametrize(('grant','expected'),[(False,0),(True,78)])
def test_optional_prestage_defers_only_missing_old_control_plane_grant(tmp_path,grant,expected):
    calls=tmp_path/'calls';marker=tmp_path/'prestage';fakebin=tmp_path/'bin';fakebin.mkdir()
    helper=fakebin/'old-or-current-runtime'
    helper.write_text('#!/bin/bash\nprintf "checked\\n" >> '+str(calls)+'\nexit '+('78' if grant else '64')+'\n');helper.chmod(0o755)
    sudo=fakebin/'sudo'
    sudo.write_text('#!/bin/bash\n[[ "$1" != -n ]] || shift\nif [[ "$1" == -l ]]; then exit '+('0' if grant else '1')+'; fi\nexec "$@"\n');sudo.chmod(0o755)
    shell=f'''set -euo pipefail
source {DEPLOY}
preflight_control_plane() {{ return 0; }}
preflight_env() {{ return 0; }}
write_mcp_env() {{ return 0; }}
write_subscription_env() {{ return 0; }}
systemctl() {{ return 1; }}
current_commit() {{ printf '%s\\n' {'b'*40}; }}
git() {{ if [[ "$*" == *"rev-parse origin/main"* ]]; then printf '%s\\n' {'a'*40}; fi; }}
verify_tracked_drift_matches_target() {{ return 0; }}
create_staged_worktree() {{ printf 'unsafe-worktree\\n' >> {calls}; }}
write_prestage() {{ touch {marker}; }}
main {'a'*40}
'''
    result=subprocess.run(['bash','-c',shell],capture_output=True,text=True,env={**os.environ,'PATH':str(fakebin)+':'+os.environ['PATH'],'RADON_APP_RUNTIME_HELPER':str(helper),'RADON_DEPLOY_STAGE':'1','RADON_DEPLOY_TRANSITION_JOURNAL':str(tmp_path/'journal'),'RADON_DEPLOY_PRESTAGE':str(marker)})
    assert result.returncode==expected,result.stdout+result.stderr
    assert not marker.exists()
    assert (calls.read_text().splitlines() if calls.exists() else [])==(['checked'] if grant else [])
    if not grant:assert 'deferr' in result.stdout.lower()


def test_documented_admission_figures_match_the_computed_budget(monkeypatch):
    api = setup_volumes(monkeypatch)
    monkeypatch.setattr(api, '_model_file_present', lambda *args: True)
    present = api.check_model_space()['filesystems'][0]
    monkeypatch.setattr(api, '_model_file_present', lambda *args: False)
    absent = api.check_model_space()['filesystems'][0]
    figures = (str(present['required_bytes']), str(absent['required_bytes'] - present['required_bytes']), str(present['required_inodes']))
    for doc in (CLOUD / 'CLAUDE.md', CLOUD.parent / 'docs/operations.md', CLOUD.parent / 'docs/model-gateway.md'):
        text = doc.read_text()
        assert '8881176576' not in text and '201161' not in text, doc
        for figure in figures:
            assert figure in text, (doc, figure)
