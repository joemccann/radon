"""Mac executable upgrades originate from a root-owned exact-release export."""
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path
import pytest
from test_model_clients_install import module,SHA

def payload(unsafe=None):
    raw=io.BytesIO()
    files={**{f'bin/{name}':b'\xcf\xfa\xed\xfe\x0c\x00\x00\x01pinned MachO' for name in ('claude','codex','grok','rg')},
           **{f'model_gateway/{name}':b'pass\n' for name in ('__init__.py','client.py','cli.py','cli_bridge.py')},
           'package.json':b'{"dependencies":{"@anthropic-ai/claude-code":"2.1.311","@openai/codex":"0.160.1","@xai-official/grok":"1.0.70"}}'}
    if unsafe:files[unsafe]=b'forbidden'
    with tarfile.open(fileobj=raw,mode='w') as archive:
        for name,data in files.items():
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o755 if name.startswith('bin/') else 0o644;archive.addfile(info,io.BytesIO(data))
    raw.seek(0);return raw

def test_root_exports_verified_darwin_release_without_promoting_failed_deploy(tmp_path):
    api=module();root=tmp_path/'exports'
    release=api.publish_darwin_bundle(payload(),root,SHA,owner_uid=os.getuid())
    manifest=json.loads((release/'manifest.json').read_text())
    assert manifest['release_sha']==SHA
    assert manifest['platform']=='darwin-arm64'
    assert manifest['sha256']==hashlib.sha256((release/'bundle.tar').read_bytes()).hexdigest()
    assert not (root/'current').exists()
    api.activate_darwin_export(root,SHA,owner_uid=os.getuid())
    assert (root/'current').resolve()==release

@pytest.mark.parametrize('unsafe',['../escape','/tmp/escape','model_gateway/engine.py','bin/shell'])
def test_darwin_export_rejects_unknown_or_escaping_payload_members(tmp_path,unsafe):
    api=module()
    with pytest.raises(ValueError):api.publish_darwin_bundle(payload(unsafe),tmp_path/'exports',SHA,owner_uid=os.getuid())
    assert not (tmp_path/'exports'/SHA).exists()

def test_darwin_export_activation_rechecks_root_owned_bundle_digest(tmp_path):
    api=module();root=tmp_path/'exports';release=api.publish_darwin_bundle(payload(),root,SHA,owner_uid=os.getuid())
    (release/'bundle.tar').chmod(0o600);(release/'bundle.tar').write_bytes(b'tampered')
    with pytest.raises(ValueError):api.activate_darwin_export(root,SHA,owner_uid=os.getuid())
    assert not (root/'current').exists()

def test_mac_export_selector_is_promoted_only_after_release_transition_commit():
    cloud=Path(__file__).resolve().parents[1]
    helper=(cloud/'scripts/deploy-root-helper.sh').read_text().rsplit('  commit-transition)\n',1)[1].split('    ;;',1)[0]
    assert helper.index('commit_transition')<helper.index('commit-model-clients')
    runtime=(cloud/'scripts/radon-app-runtime.sh').read_text()
    assert 'commit-model-clients' in runtime


def test_repeated_directory_headers_count_toward_export_bound():
    original=payload();raw=io.BytesIO()
    with tarfile.open(fileobj=raw,mode='w') as archive:
        for _ in range(33):
            info=tarfile.TarInfo('bin');info.type=tarfile.DIRTYPE;archive.addfile(info)
    raw.seek(0)
    with pytest.raises(ValueError,match='bounds'):module()._darwin_payload(raw)
