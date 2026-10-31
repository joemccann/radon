#!/usr/bin/env python3
"""Provision the fixed model-only SSH identity and read-only export chroot.

Root-owned control plane. The SSH account receives no shell, TCP/agent/X11/TTY,
no writable export path, and no access to provider grants or management/vault
sockets. Its only host filesystem capability is the narrow inference group.
"""
from __future__ import annotations
import base64
import grp
import os
import pwd
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
ACCOUNT='radon-model-tunnel'
GROUP='radon-model-connect'
EXPORT=Path('/var/lib/radon-model-export')
PRIVATE=Path('/etc/radon/model-transport')
INFERENCE=Path('/run/radon-model-gateway')
SSHD_CONFIG=Path('/etc/ssh/sshd_config.d/60-radon-model-transport.conf')
ENVIRONMENT=Path('/etc/radon/env')
SAFE_ENV={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','LANG':'C.UTF-8'}

def sshd_config():
    return '''# Managed model-only inference tunnel and immutable client export.
# OpenSSH permits this injection control only in global scope.
PermitUserEnvironment no
Match User radon-model-tunnel
    ChrootDirectory /var/lib/radon-model-export
    ForceCommand internal-sftp -R -d /releases
    AuthorizedKeysFile /etc/radon/model-transport/authorized_keys
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    # OpenSSH 9.6 shares local TCP/Unix admission; the only permitted host
    # is this absolute Unix path, which cannot name a network destination.
    AllowTcpForwarding local
    PermitOpen [/run/radon-model-gateway/gateway.sock]:*
    AllowStreamLocalForwarding local
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
    PermitTunnel no
    PermitUserRC no
Match all
'''

def authorized_key(value):
    fields=value.strip().split()
    if len(fields)<2 or fields[0]!='ssh-ed25519' or '\n' in value or '\r' in value:
        raise ValueError('only a single Ed25519 public key is accepted')
    raw=base64.b64decode(fields[1],validate=True)
    if len(raw)!=51 or raw[:19]!=b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20':
        raise ValueError('invalid Ed25519 public key')
    # Global SSH policy denies TCP; this re-enables only configured forwarding
    # after restrict, while all command sessions are forced into read-only SFTP.
    return 'restrict,port-forwarding '+fields[0]+' '+fields[1]+'\n'

def gateway_environment(original,uid,gid):
    if type(uid)is not int or type(gid)is not int or uid<=0 or gid<=0 or uid==1000 or gid==1000:
        raise ValueError('invalid model-only identity')
    updates={'RADON_MODEL_TUNNEL_UID':str(uid),'RADON_MODEL_TUNNEL_GID':str(gid)}
    lines=[]
    for line in original.splitlines():
        key=line.partition('=')[0]
        if key not in updates:lines.append(line)
    return '\n'.join(lines+[key+'='+value for key,value in updates.items()])+'\n'

def trusted(path,*,directory=False):
    path=Path(path)
    for ancestor in reversed(path.parents):
        info=ancestor.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise ValueError('untrusted model transport ancestor')
    info=path.lstat()
    kind=stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
        raise ValueError('untrusted model transport path')

def root_directory(path,mode=0o755):
    if not path.exists() and not path.is_symlink():
        trusted(path.parent,directory=True);path.mkdir(mode=mode)
    trusted(path,directory=True)
    path.chmod(mode)

def atomic(path,raw,mode=0o644,*,gid=0):
    trusted(path.parent,directory=True)
    if path.exists() or path.is_symlink():trusted(path)
    descriptor,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(raw);stream.flush();os.fchmod(stream.fileno(),mode);os.fchown(stream.fileno(),0,gid);os.fsync(stream.fileno())
        os.replace(temporary,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if temporary.exists():temporary.unlink()

def run(command,*,check=True):
    return subprocess.run(command,check=check,capture_output=True,timeout=30,env=SAFE_ENV)

def validate_inference_source(info,gid):
    if gid in {0,1000} or not stat.S_ISDIR(info.st_mode) or info.st_uid!=1000 or info.st_gid!=gid or stat.S_IMODE(info.st_mode)!=0o710:
        raise ValueError('untrusted inference socket directory')


def mount_info(target):
    # A mount record, unlike ismount(), recognizes a same-filesystem bind.
    records=[]
    with open('/proc/self/mountinfo',encoding='utf-8') as stream:
        for line in stream:
            fields=line.split()
            if len(fields)>6 and fields[4]==str(target):records.append(set(fields[5].split(',')))
    if len(records)>1:raise ValueError('stacked inference export mounts')
    return records[0] if records else None


def bind_inference():
    # No caller-provided paths, account IDs, mount options, or secret access.
    trusted(INFERENCE.parent,directory=True)
    source_fd=os.open(INFERENCE,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        source=os.fstat(source_fd)
        validate_inference_source(source,grp.getgrnam(GROUP).gr_gid)
        root_directory(EXPORT)
        root_directory(EXPORT/'run')
        target=EXPORT/'run/radon-model-gateway'
        trusted(target.parent,directory=True)
        options=mount_info(target)
        if options is None:
            root_directory(target)
            run(['/usr/bin/mount','--bind',str(INFERENCE),str(target)])
            try:
                run(['/usr/bin/mount','-o','remount,bind,ro,nosuid,nodev,noexec',str(target)])
            except Exception:
                run(['/usr/bin/umount',str(target)],check=False)
                raise
            options=mount_info(target)
        # A restart may reuse only this exact source inode and protected bind.
        # Reboot clears /run and the mount, so the next start recreates both.
        target_fd=os.open(target,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:mounted=os.fstat(target_fd)
        finally:os.close(target_fd)
        if options is None or not {'ro','nosuid','nodev','noexec'}<=options or (source.st_dev,source.st_ino)!=(mounted.st_dev,mounted.st_ino):
            raise ValueError('inference export mount identity or protection changed')
    finally:os.close(source_fd)


def prepare(public_key):
    key=authorized_key(public_key)
    root_directory(EXPORT);root_directory(EXPORT/'releases')
    root_directory(PRIVATE)
    try:group=grp.getgrnam(GROUP)
    except KeyError:
        run(['/usr/sbin/groupadd','--system',GROUP]);group=grp.getgrnam(GROUP)
    try:account=pwd.getpwnam(ACCOUNT)
    except KeyError:
        run(['/usr/sbin/useradd','--system','--gid',GROUP,'--no-create-home','--home-dir',str(EXPORT),'--shell','/usr/sbin/nologin','--password','*',ACCOUNT]);account=pwd.getpwnam(ACCOUNT)
    if group.gr_gid in {0,1000} or account.pw_uid in {0,1000} or account.pw_gid!=group.gr_gid or account.pw_dir!=str(EXPORT) or account.pw_shell!='/usr/sbin/nologin' or set(os.getgrouplist(ACCOUNT,account.pw_gid))!={group.gr_gid}:
        raise ValueError('model-only SSH account has unexpected privileges')
    trusted(ENVIRONMENT)
    meta=ENVIRONMENT.stat()
    original=ENVIRONMENT.read_text()
    updated=gateway_environment(original,account.pw_uid,group.gr_gid)
    old_config=SSHD_CONFIG.read_bytes() if SSHD_CONFIG.exists() else None
    atomic(PRIVATE/'authorized_keys',key.encode())
    atomic(SSHD_CONFIG,sshd_config().encode())
    try:
        run(['/usr/sbin/sshd','-t','-f','/etc/ssh/sshd_config'])
    except Exception:
        if old_config is None:SSHD_CONFIG.unlink()
        else:atomic(SSHD_CONFIG,old_config)
        raise
    atomic(ENVIRONMENT,updated.encode(),stat.S_IMODE(meta.st_mode),gid=meta.st_gid)
    run(['/usr/bin/systemctl','reload','ssh.service'])
    print('model-only SSH identity provisioned; restart only the model gateway to apply inference peer admission')

def main(arguments):
    if os.geteuid()!=0 or not ((arguments[:1]==['prepare'] and len(arguments)==2) or arguments==['bind-inference']):return 64
    try:
        trusted(Path(__file__))
        if arguments==['bind-inference']:bind_inference()
        else:prepare(arguments[1])
        return 0
    except Exception as exc:
        print('model transport provisioning refused: '+type(exc).__name__,file=sys.stderr)
        return 78

if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
