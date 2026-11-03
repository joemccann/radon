"""Recovery reuses exact immutable receipts without allocating another payload."""
import json
import os
from pathlib import Path

import pytest

from test_model_clients_install import module, payload, SHA
from test_model_clients_darwin import payload as darwin_payload


def installed(tmp_path):
    api=module();root=tmp_path/'clients';exports=tmp_path/'exports';exports.mkdir()
    api.install_from_tar(payload(),root,SHA,owner_uid=os.getuid())
    api.publish_darwin_bundle(darwin_payload(),exports/'releases',SHA,owner_uid=os.getuid())
    return api,root,exports


def test_recovery_reuses_verified_target_without_export_or_disk_threshold(tmp_path,monkeypatch):
    api,root,exports=installed(tmp_path)
    monkeypatch.setattr(api,'ROOT',root);monkeypatch.setattr(api,'EXPORT_ROOT',exports)
    monkeypatch.setattr(api.os,'geteuid',lambda:0)
    monkeypatch.setattr(api,'check_model_space',lambda:pytest.fail('recovery must not require another payload allocation'))
    monkeypatch.setattr(api,'export_from_image',lambda *args,**kwargs:pytest.fail('recovery must not export'))
    # Fixture ownership is the test user; production's CLI fixes this to UID0.
    original=api.recover_clients
    monkeypatch.setattr(api,'recover_clients',lambda release:original(release,owner_uid=os.getuid()))
    assert api.main(['recover',SHA])==0
    assert (root/'current').resolve()==root/SHA
    assert not (exports/'releases/current').exists()


@pytest.mark.parametrize('damage',['missing','linux-hash','helper-hash','receipt-sha','darwin-hash','writable-directory'])
def test_recovery_refuses_missing_or_corrupt_existing_artifacts(tmp_path,damage):
    api,root,exports=installed(tmp_path)
    if damage=='missing': target=SHA.replace('a','b')
    else:
        target=SHA
        if damage=='writable-directory':(root/SHA/'lib').chmod(0o755)
        else:
            path={'linux-hash':root/SHA/'bin/grok','helper-hash':root/SHA/'lib/model_gateway/client.py','receipt-sha':root/SHA/'manifest.json','darwin-hash':exports/'releases'/SHA/'bundle.tar'}[damage]
            path.chmod(0o644)
            if damage=='receipt-sha':
                body=json.loads(path.read_text());body['release_sha']='b'*40;path.write_text(json.dumps(body))
            else:path.write_bytes(b'altered')
            path.chmod(0o444)
    with pytest.raises((ValueError,OSError)):
        api.recover_clients(target,root=root,exports=exports,owner_uid=os.getuid())
    assert (root/'current').resolve()==root/SHA
    assert not (exports/'releases/current').exists()


def test_recovery_reselects_verified_old_linux_payload_without_activating_mac_export(tmp_path):
    api,root,exports=installed(tmp_path)
    other='b'*40
    api.install_from_tar(payload(),root,other,owner_uid=os.getuid())
    api.recover_clients(SHA,root=root,exports=exports,owner_uid=os.getuid())
    assert (root/'current').resolve()==root/SHA
    assert not (exports/'releases/current').exists()


def test_root_recovery_uses_fixed_receipt_reuse_before_start():
    cloud=Path(__file__).resolve().parents[1]
    helper=(cloud/'scripts/deploy-root-helper.sh').read_text()
    recover=helper.split('  recover)\n',1)[1].split('    ;;',1)[0]
    assert 'install-model-clients' not in recover
    assert recover.index('recover-model-clients')<recover.index('"${CORE_SERVICES[@]}"')
