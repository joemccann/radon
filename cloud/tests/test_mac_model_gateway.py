"""The Mac sees only model transport and root-verified consumer binaries."""
import importlib.util
import io
import json
import os
import tarfile
from pathlib import Path
import pytest
from test_model_clients_darwin import payload
from test_model_clients_install import SHA
CLOUD=Path(__file__).resolve().parents[1]

def api():
    spec=importlib.util.spec_from_file_location('mac_gateway',CLOUD/'scripts/mac_model_gateway.py')
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

def manifest(raw):
    import hashlib
    files={}
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive:
            if member.isfile():
                data=archive.extractfile(member).read();files[member.name]={'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)}
    return {'version':1,'platform':'darwin-arm64','release_sha':SHA,'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),'files':files,'cli_versions':{'claude':'2.1.311','codex':'0.160.1','grok':'1.0.70'}}

def test_installs_only_verified_consumer_payload_and_checks_apple_signatures(tmp_path):
    module=api();raw=payload().read();proof=manifest(raw);archive=tmp_path/'bundle.tar';archive.write_bytes(raw)
    checked=[]
    root=tmp_path/'clients'
    path=module.install_bundle(archive,proof,root,owner_uid=os.getuid(),verify_signature=lambda p:checked.append(p.name))
    assert (root/'current').resolve()==path
    assert set(checked)=={'claude','codex','grok','rg'}
    assert (path/'lib/model_gateway/client.py').is_file()
    assert not (path/'lib/model_gateway/engine.py').exists()
    assert not (path/'bin/grok').stat().st_mode & 0o222

@pytest.mark.parametrize('damage',['hash','platform','release','signature','unknown_member'])
def test_failed_mac_promotion_retains_current_selector(tmp_path,damage):
    module=api();raw=payload().read();proof=manifest(raw);archive=tmp_path/'bundle.tar';archive.write_bytes(raw)
    root=tmp_path/'clients';old=module.install_bundle(archive,proof,root,owner_uid=os.getuid(),verify_signature=lambda p:None)
    proof['release_sha']='b'*40
    if damage=='hash':proof['sha256']='f'*64
    elif damage=='platform':proof['platform']='linux-amd64'
    elif damage=='release':proof['release_sha']='../unsafe'
    elif damage=='unknown_member':raw=payload('model_gateway/engine.py').read();proof=manifest(raw);archive.write_bytes(raw)
    def verify(path):
        if damage=='signature':raise ValueError('signature invalid')
    with pytest.raises((ValueError,OSError)):module.install_bundle(archive,proof,root,owner_uid=os.getuid(),verify_signature=verify)
    assert (root/'current').resolve()==old


def test_supervised_tunnel_uses_only_dedicated_key_pinned_host_and_private_uds():
    module=api();config={'host':'model.example.test','user':'radon-model-tunnel'}
    plist=module.tunnel_plist(config)
    command=plist['ProgramArguments'];joined=' '.join(command)
    assert command[0]=='/usr/bin/ssh'
    assert '/Library/Radon/model-gateway/identity' in command
    assert 'StrictHostKeyChecking=yes' in command
    assert 'UserKnownHostsFile=/Library/Radon/model-gateway/known_hosts' in command
    assert 'ExitOnForwardFailure=yes' in command
    assert '/Library/Radon/model-gateway/gateway.sock:/run/radon-model-gateway/gateway.sock' in command
    assert plist['KeepAlive'] is True and plist['UserName']=='root'
    assert 'id_ed25519' not in joined and 'RADON_SERVICE_TOKEN' not in joined


@pytest.mark.parametrize('damage',['writable','symlink'])
def test_existing_release_internal_ancestors_must_be_immutable_root_owned_directories(tmp_path,damage):
    module=api();raw=payload().read();proof=manifest(raw);archive=tmp_path/'bundle.tar';archive.write_bytes(raw)
    root=tmp_path/'clients';installed=module.install_bundle(archive,proof,root,owner_uid=os.getuid(),verify_signature=lambda p:None)
    if damage=='writable':(installed/'bin').chmod(0o777)
    else:
        installed.chmod(0o755);outside=installed/'outside';(installed/'bin').rename(outside);(installed/'bin').symlink_to(outside,target_is_directory=True);installed.chmod(0o555)
    with pytest.raises(ValueError):module.install_bundle(archive,proof,root,owner_uid=os.getuid(),verify_signature=lambda p:None)
    assert (root/'current').resolve()==installed
    for folder,_,_ in os.walk(installed,followlinks=False):os.chmod(folder,0o700)


def test_repeated_directory_headers_count_toward_mac_bundle_bound(tmp_path):
    module=api();original=payload().read();raw=io.BytesIO()
    with tarfile.open(fileobj=raw,mode='w') as archive:
        for _ in range(33):
            info=tarfile.TarInfo('bin');info.type=tarfile.DIRTYPE;archive.addfile(info)
    data=raw.getvalue();proof=manifest(original);proof['sha256']=__import__('hashlib').sha256(data).hexdigest();proof['size']=len(data);target=tmp_path/'bundle.tar';target.write_bytes(data)
    with pytest.raises(ValueError,match='bounds'):module.install_bundle(target,proof,tmp_path/'clients',owner_uid=os.getuid(),verify_signature=lambda p:None)


def test_mac_upgrade_burst_retains_recently_demoted_native_sessions(tmp_path):
    module=api();raw=payload().read();archive=tmp_path/'bundle.tar';archive.write_bytes(raw);root=tmp_path/'clients'
    for number in range(1,8):
        proof=manifest(raw);proof['release_sha']=f'{number:040x}'
        module.install_bundle(archive,proof,root,owner_uid=os.getuid(),verify_signature=lambda p:None)
    assert len([p for p in root.iterdir() if len(p.name)==40])==7
