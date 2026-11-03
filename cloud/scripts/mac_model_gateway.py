#!/usr/bin/env python3
"""Root-owned Mac client promotion and supervised model-only transport.

Root fetches only the read-only export chroot, over a dedicated key and pinned
host. Provider authentication and inference remain in the Linux gateway. This
module uses stdlib compatible with Apple's system Python; it never runs vendor
installers, login, application modules, or an operator's SSH/GitHub credentials.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import plistlib
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
BASE=Path('/Library/Radon')
TRANSPORT=BASE/'model-gateway'
CLIENTS=BASE/'model-clients'
CONFIG=TRANSPORT/'config.json'
GROUP='_radonmodel'
USER='radon-model-tunnel'
MAX_BYTES=2*1024*1024*1024
HELPERS=('__init__.py','client.py','cli.py','cli_bridge.py')
COMMANDS=('claude','codex','grok','rg')
FILES={'package.json',*('bin/'+name for name in COMMANDS),*('model_gateway/'+name for name in HELPERS)}
LABEL='com.radon.model-gateway'

def digest(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while True:
            chunk=stream.read(1048576)
            if not chunk:break
            result.update(chunk)
    return result.hexdigest()

def trusted(path,owner_uid=0,*,directory=False):
    path=Path(path)
    for ancestor in reversed(path.parents):
        info=ancestor.lstat()
        # Non-root owner injection is available only to unit-test callers.
        # Production immutable paths never accept writable ancestors.
        test_tmp=owner_uid!=0 and info.st_uid==0 and info.st_mode&stat.S_ISVTX and str(ancestor) in {'/tmp','/private/tmp'}
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0,owner_uid} or (info.st_mode&0o022 and not test_tmp):
            raise ValueError('untrusted model client ancestor')
    info=path.lstat()
    kind=stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid!=owner_uid or info.st_mode&0o022:
        raise ValueError('untrusted model client path')

def root_directory(path,owner_uid=0,mode=0o755):
    path=Path(path)
    trusted(path.parent,owner_uid if path.parent.stat().st_uid==owner_uid else 0,directory=True)
    if not path.exists() and not path.is_symlink():path.mkdir(mode=mode)
    trusted(path,owner_uid,directory=True)

def trusted_tree(path,owner_uid=0):
    trusted(path,owner_uid,directory=True)
    for folder,dirs,files in os.walk(path,followlinks=False):
        for name in dirs:trusted(Path(folder)/name,owner_uid,directory=True)
        for name in files:trusted(Path(folder)/name,owner_uid)

def atomic(path,raw,mode=0o600):
    trusted(path.parent,directory=True)
    descriptor,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(raw);stream.flush();os.fchmod(stream.fileno(),mode);os.fsync(stream.fileno())
        os.replace(temporary,path)
        parent=os.open(path.parent,os.O_RDONLY);os.fsync(parent);os.close(parent)
    finally:
        if temporary.exists():temporary.unlink()

def signature(path):
    subprocess.run(['/usr/bin/codesign','--verify','--strict','--all-architectures',str(path)],check=True,capture_output=True,timeout=30)
    subprocess.run(['/usr/bin/lipo','-verify_arch','arm64',str(path)],check=True,capture_output=True,timeout=10)

def _remove_stage(path):
    if not path.exists():return
    for folder,dirs,files in os.walk(path):
        os.chmod(folder,0o700)
    shutil.rmtree(path)

def install_bundle(archive,proof,root=CLIENTS,*,owner_uid=0,verify_signature=signature):
    root=Path(root);root_directory(root,owner_uid)
    release=proof.get('release_sha')
    if proof.get('version')!=1 or proof.get('platform')!='darwin-arm64' or not isinstance(release,str) or not re.fullmatch(r'[0-9a-f]{40}',release):
        raise ValueError('invalid Mac release identity')
    archive=Path(archive)
    if not archive.is_file() or archive.is_symlink() or archive.stat().st_size>MAX_BYTES or proof.get('size')!=archive.stat().st_size or proof.get('sha256')!=digest(archive):
        raise ValueError('Mac bundle digest mismatch')
    if set(proof.get('files',{}))!=FILES:raise ValueError('Mac bundle manifest is incomplete')
    versions=proof.get('cli_versions',{})
    if set(versions)!={'claude','codex','grok'} or any(not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',value) for value in versions.values()):
        raise ValueError('Mac client versions are not pinned')
    stage=Path(tempfile.mkdtemp(prefix='.stage-',dir=root));target=root/release
    try:
        seen=set();total=count=0
        with tarfile.open(archive,mode='r|') as stream:
            for member in stream:
                count+=1
                if count>32:raise ValueError('Mac bundle exceeds bounds')
                name=Path(member.name).as_posix().removeprefix('./')
                if member.isdir() and name in {'.','bin','model_gateway'}:continue
                if name not in FILES or name in seen or not member.isfile():raise ValueError('unsafe Mac bundle member')
                total+=member.size
                if count>32 or total>MAX_BYTES or member.size<=0:raise ValueError('Mac bundle exceeds bounds')
                expected=proof['files'][name]
                if member.size!=expected['size']:raise ValueError('Mac bundle member size mismatch')
                destination=stage/name;destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                incoming=stream.extractfile(member)
                if incoming is None:raise ValueError('missing Mac bundle data')
                with destination.open('xb') as output:
                    shutil.copyfileobj(incoming,output,1048576);output.flush();os.fsync(output.fileno())
                if digest(destination)!=expected['sha256']:raise ValueError('Mac bundle member digest mismatch')
                destination.chmod(0o555 if name.startswith('bin/') else 0o444);seen.add(name)
        if seen!=FILES:raise ValueError('Mac bundle is incomplete')
        packages=json.loads((stage/'package.json').read_text())['dependencies']
        for command,package in [('claude','@anthropic-ai/claude-code'),('codex','@openai/codex'),('grok','@xai-official/grok')]:
            if packages.get(package)!=versions[command]:raise ValueError('Mac package versions mismatch')
        for name in COMMANDS:verify_signature(stage/'bin'/name)
        # Unchanged native clients are immutable root-owned bytes. Reuse their
        # inodes across release receipts instead of copying hundreds of MB for
        # every application-only release.
        current=root/'current'
        if current.is_symlink() and re.fullmatch(r'[0-9a-f]{40}',os.readlink(current)):
            previous=root/os.readlink(current)
            trusted(previous,owner_uid,directory=True)
            for name,row in proof['files'].items():
                old=previous/('lib/'+name if name.startswith('model_gateway/') else name)
                if old.exists() and not old.is_symlink():
                    trusted(old,owner_uid)
                    if digest(old)==row['sha256']:
                        temporary=(stage/name).with_name('.shared-'+os.urandom(6).hex())
                        os.link(old,temporary);os.replace(temporary,stage/name)
        (stage/'lib').mkdir();os.rename(stage/'model_gateway',stage/'lib/model_gateway')
        (stage/'manifest.json').write_text(json.dumps(proof,sort_keys=True)+'\n');(stage/'manifest.json').chmod(0o444)
        for folder,dirs,files in os.walk(stage):os.chmod(folder,0o555)
        if target.exists() or target.is_symlink():
            trusted(target,owner_uid,directory=True)
            trusted(target/'manifest.json',owner_uid)
            if json.loads((target/'manifest.json').read_text())!=proof:raise ValueError('Mac release conflicts with existing receipt')
            for name,row in proof['files'].items():
                installed=target/('lib/'+name if name.startswith('model_gateway/') else name)
                trusted(installed,owner_uid)
                if digest(installed)!=row['sha256']:raise ValueError('installed Mac client differs from receipt')
        else:os.rename(stage,target)
        selector=root/'current'
        if selector.exists() and not selector.is_symlink():raise ValueError('Mac client selector is not a symlink')
        if selector.is_symlink():
            previous=os.readlink(selector)
            if not re.fullmatch(r'[0-9a-f]{40}',previous):raise ValueError('invalid previous Mac selector')
            if previous!=release:
                trusted_tree(root/previous,owner_uid);os.utime(root/previous,None)
        temporary=root/('.current-'+os.urandom(8).hex())
        try:
            temporary.symlink_to(release);os.replace(temporary,selector)
            directory=os.open(root,os.O_RDONLY);os.fsync(directory);os.close(directory)
        finally:
            if temporary.is_symlink():temporary.unlink()
        candidates=[]
        for entry in root.iterdir():
            if re.fullmatch(r'[0-9a-f]{40}',entry.name) and entry.name!=release and entry.is_dir() and not entry.is_symlink():
                trusted(entry,owner_uid,directory=True);candidates.append(entry)
        # Runner sessions are bounded to an hour; retain four older receipts.
        for stale in sorted(candidates,key=lambda item:item.stat().st_mtime,reverse=True)[4:]:
            if stale.stat().st_mtime>=time.time()-7200:continue
            trusted_tree(stale,owner_uid)
            _remove_stage(stale)
        return target
    finally:_remove_stage(stage)

def ssh_arguments(config):
    host=config.get('host','')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}',host) or config.get('user')!=USER:
        raise ValueError('invalid model transport destination')
    return ['-F','/dev/null','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','IdentityAgent=none','-o','ForwardAgent=no','-o','AddKeysToAgent=no','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(TRANSPORT/'known_hosts'),'-i',str(TRANSPORT/'identity')]

def tunnel_plist(config):
    arguments=ssh_arguments(config)
    return {'Label':LABEL+'-tunnel','ProgramArguments':['/usr/bin/ssh',*arguments,'-NT','-o','ExitOnForwardFailure=yes','-o','StreamLocalBindUnlink=yes','-o','StreamLocalBindMask=0117','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-L',str(TRANSPORT/'gateway.sock')+':/run/radon-model-gateway/gateway.sock',USER+'@'+config['host']],
            'UserName':'root','GroupName':GROUP,'KeepAlive':True,'RunAtLoad':True,'ThrottleInterval':10,'StandardOutPath':'/Library/Radon/model-logs/model-tunnel.log','StandardErrorPath':'/Library/Radon/model-logs/model-tunnel.log'}

def updater_plist():
    return {'Label':LABEL+'-updater','ProgramArguments':['/usr/bin/python3','-I',str(TRANSPORT/'manager.py'),'update'],'UserName':'root','RunAtLoad':True,'StartInterval':1800,'ProcessType':'Background','StandardOutPath':'/Library/Radon/model-logs/model-clients.log','StandardErrorPath':'/Library/Radon/model-logs/model-clients.log'}

def read_config():
    root_directory(BASE);root_directory(TRANSPORT);root_directory(CLIENTS)
    trusted(CONFIG)
    config=json.loads(CONFIG.read_text());ssh_arguments(config)
    for path in (TRANSPORT/'identity',TRANSPORT/'known_hosts'):trusted(path)
    if (TRANSPORT/'identity').stat().st_mode&0o077:raise ValueError('model transport key is not private')
    return config

def _sftp(config,commands,*,timeout=180):
    import resource
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE,(MAX_BYTES,MAX_BYTES))
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    with tempfile.NamedTemporaryFile(mode='w',prefix='radon-model-sftp-',delete=False) as stream:
        batch=Path(stream.name);stream.write(commands);stream.flush()
    try:
        subprocess.run(['/usr/bin/sftp',*ssh_arguments(config),'-q','-b',str(batch),USER+'@'+config['host']],check=True,capture_output=True,timeout=timeout,preexec_fn=limits,env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','HOME':'/var/root','LC_ALL':'C'})
    finally:batch.unlink()

def update():
    config=read_config()
    with tempfile.TemporaryDirectory(prefix='radon-model-client-') as name:
        folder=Path(name);manifest=folder/'manifest.json';archive=folder/'bundle.tar'
        _sftp(config,'get /releases/current/manifest.json "'+str(manifest)+'"\n',timeout=30)
        if manifest.stat().st_size>1_048_576:raise ValueError('model manifest exceeds bounds')
        proof=json.loads(manifest.read_text());release=proof.get('release_sha','')
        if not isinstance(release,str) or not re.fullmatch(r'[0-9a-f]{40}',release):raise ValueError('invalid model release')
        if not isinstance(proof.get('size'),int) or not 0<proof['size']<=MAX_BYTES:raise ValueError('invalid model bundle size')
        current=CLIENTS/'current'
        installed=CLIENTS/release
        if current.is_symlink() and os.readlink(current)==release and installed.is_dir():
            trusted(installed,directory=True);trusted(installed/'manifest.json')
            if json.loads((installed/'manifest.json').read_text())==proof:
                for path,row in proof.get('files',{}).items():
                    if path not in FILES:raise ValueError('invalid installed model member')
                    local=installed/('lib/'+path if path.startswith('model_gateway/') else path)
                    trusted(local)
                    if digest(local)!=row['sha256']:raise ValueError('installed model client digest mismatch')
                if set(proof.get('files',{}))!=FILES:raise ValueError('installed model receipt is incomplete')
                print('model clients current and verified: '+release)
                return
        _sftp(config,'get /releases/'+release+'/bundle.tar "'+str(archive)+'"\n')
        installed=install_bundle(archive,proof)
    print('model clients verified and promoted: '+installed.name)

def prepare(host,host_key,operator):
    config={'host':host,'user':USER};ssh_arguments(config)
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]{68}={0,2}',host_key):raise ValueError('an authenticated Ed25519 host key is required')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}',operator) or pwd.getpwnam(operator).pw_uid==0:raise ValueError('invalid operator account')
    encoded=host_key.split()[1]
    decoded=base64.b64decode(encoded,validate=True)
    if len(decoded)!=51 or decoded[:19]!=b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20':raise ValueError('invalid Ed25519 host key')
    trusted(Path(__file__))
    root_directory(BASE);root_directory(TRANSPORT,mode=0o710);root_directory(CLIENTS)
    existing=subprocess.run(['/usr/sbin/dseditgroup','-o','read',GROUP],capture_output=True)
    if existing.returncode:subprocess.run(['/usr/sbin/dseditgroup','-o','create',GROUP],check=True,capture_output=True)
    for account in (operator,'_radonbot'):
        pwd.getpwnam(account)
        subprocess.run(['/usr/sbin/dseditgroup','-o','edit','-a',account,'-t','user',GROUP],check=True,capture_output=True)
    import grp
    os.chown(TRANSPORT,0,grp.getgrnam(GROUP).gr_gid);TRANSPORT.chmod(0o710)
    atomic(TRANSPORT/'manager.py',Path(__file__).read_bytes(),0o555)
    atomic(CONFIG,(json.dumps(config,sort_keys=True)+'\n').encode())
    atomic(TRANSPORT/'known_hosts',(host+' '+host_key+'\n').encode())
    if not (TRANSPORT/'identity').exists():
        subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(TRANSPORT/'identity')],check=True,capture_output=True)
    trusted(TRANSPORT/'identity')
    if (TRANSPORT/'identity').stat().st_mode&0o077:raise ValueError('model transport key is not private')
    print((TRANSPORT/'identity.pub').read_text().strip())

def activate():
    config=read_config();update()
    root_directory(BASE/'model-logs')
    for plist in (tunnel_plist(config),updater_plist()):
        destination=Path('/Library/LaunchDaemons')/(plist['Label']+'.plist')
        raw=plistlib.dumps(plist)
        unchanged=destination.exists() and not destination.is_symlink() and destination.read_bytes()==raw
        if not unchanged:
            atomic(destination,raw,0o644)
            subprocess.run(['/bin/launchctl','bootout','system/'+plist['Label']],check=False,capture_output=True)
        state=subprocess.run(['/bin/launchctl','print','system/'+plist['Label']],capture_output=True)
        if state.returncode:
            subprocess.run(['/bin/launchctl','bootstrap','system',str(destination)],check=True,capture_output=True)
    print('model-only transport and pinned client updater active')

def main(arguments):
    if os.geteuid()!=0 or sys.platform!='darwin':return 77
    try:
        if len(arguments)==4 and arguments[0]=='prepare':prepare(*arguments[1:])
        elif arguments==['activate']:activate()
        elif arguments==['update']:update()
        else:return 64
        return 0
    except Exception as exc:
        print('Mac model gateway operation failed: '+type(exc).__name__,file=sys.stderr)
        return 78

if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
