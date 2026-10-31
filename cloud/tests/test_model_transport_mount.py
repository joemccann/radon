"""Only the fixed inference directory may enter the read-only export chroot."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

CLOUD=Path(__file__).resolve().parents[1]


def module():
    spec=importlib.util.spec_from_file_location('model_transport',CLOUD/'scripts/model_transport.py')
    api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api);return api


@pytest.mark.parametrize('mode,uid,gid',[(0o40777,1000,2002),(0o40710,0,2002),(0o40710,1000,1000),(0o120710,1000,2002)])
def test_source_rejects_writable_wrong_owner_reserved_group_and_symlink(mode,uid,gid):
    with pytest.raises(ValueError):
        module().validate_inference_source(SimpleNamespace(st_mode=mode,st_uid=uid,st_gid=gid),2002)


def test_mount_is_fixed_read_only_and_reused_only_for_same_source(tmp_path,monkeypatch):
    api=module()
    source=tmp_path/'source';source.mkdir()
    export=tmp_path/'export';export.mkdir()
    target=export/'run/radon-model-gateway'
    monkeypatch.setattr(api,'INFERENCE',source)
    monkeypatch.setattr(api,'EXPORT',export)
    monkeypatch.setattr(api.grp,'getgrnam',lambda name:SimpleNamespace(gr_gid=2002))
    trusted=[]
    monkeypatch.setattr(api,'trusted',lambda path,**kw:trusted.append(Path(path)))
    validated=[]
    monkeypatch.setattr(api,'validate_inference_source',lambda info,gid:validated.append(gid))
    mounted={}
    monkeypatch.setattr(api,'mount_info',lambda path:mounted.get(Path(path)))
    original_stat=api.os.stat
    original_fstat=api.os.fstat
    def fstat(fd):
        info=original_fstat(fd)
        if target in mounted and info.st_ino==original_stat(target).st_ino:
            return original_stat(source)
        return info
    monkeypatch.setattr(api.os,'fstat',fstat)
    commands=[]
    def run(command,**kwargs):
        commands.append([str(x) for x in command])
        if '--bind' in command:mounted[target]={'rw'}
        elif 'remount,bind,ro,nosuid,nodev,noexec' in command:
            mounted[target]={'ro','nosuid','nodev','noexec'}
    monkeypatch.setattr(api,'run',run)
    api.bind_inference()
    assert commands==[
        ['/usr/bin/mount','--bind',str(source),str(target)],
        ['/usr/bin/mount','-o','remount,bind,ro,nosuid,nodev,noexec',str(target)],
    ]
    assert source.parent in trusted and target.parent in trusted
    assert validated==[2002]
    api.bind_inference()
    assert len(commands)==2  # Restart keeps the same directory/socket bind.
    mounted.clear()  # Reboot clears the ephemeral mount; next start recreates it.
    api.bind_inference()
    assert len(commands)==4
    mounted[target]={'rw'}
    with pytest.raises(ValueError):api.bind_inference()
    assert len(commands)==4  # Never repair an untrusted existing mount in place.


def test_bind_cli_refuses_caller_paths(monkeypatch):
    api=module()
    monkeypatch.setattr(api.os,'geteuid',lambda:0)
    assert api.main(['bind-inference','/run/radon-model-control'])==64
