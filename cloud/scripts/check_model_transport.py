#!/usr/bin/env python3
"""Ephemeral GitHub-runner proof of the model-only SSH/SFTP boundary.

No production configuration, host credential, application module, broker or
external service is used. The isolated sshd binds loopback; every key is fresh.
"""
from __future__ import annotations
import grp
import importlib.util
import json
import os
import pwd
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
ACCOUNT='radon-model-tunnel'
GROUP='radon-model-transport-ci'
ENV={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','LANG':'C.UTF-8','HOME':'/root'}

def run(command,*,check=True,timeout=10):
    # This fixture owns every command and uses only fresh fixture keys. Report
    # the executable and bounded stderr, never argv or stdout (key material).
    executable=Path(str(command[0])).name
    try:
        return subprocess.run([str(x) for x in command],check=check,capture_output=True,timeout=timeout,env=ENV)
    except subprocess.CalledProcessError as exc:
        detail=(exc.stderr or b'').decode('utf-8',errors='replace')[:2048]
        detail=''.join(character for character in detail if character.isprintable() or character=='\n').strip()
        raise RuntimeError(f'fixture {executable} exited {exc.returncode}: {detail}') from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'fixture {executable} timed out after {timeout}s') from None

def main():
    if os.geteuid()!=0 or sys.platform!='linux' or os.environ.get('GITHUB_ACTIONS')!='true':
        raise RuntimeError('this root integration fixture is GitHub-runner-only')
    if not Path('/usr/sbin/sshd').is_file():raise RuntimeError('openssh-server fixture dependency missing')
    try:pwd.getpwnam(ACCOUNT)
    except KeyError:pass
    else:raise RuntimeError('refusing to modify an existing model-only account')
    root=Path('/run')/('radon-model-transport-check.'+str(os.getpid()))
    root.mkdir(mode=0o755)
    created_user=created_group=False
    processes=[];listeners=[];mounted_target=None
    try:
        run(['/usr/sbin/groupadd','--system',GROUP]);created_group=True
        run(['/usr/sbin/useradd','--system','--gid',GROUP,'--no-create-home','--home-dir','/nonexistent','--shell','/usr/sbin/nologin','--password','*',ACCOUNT]);created_user=True
        account=pwd.getpwnam(ACCOUNT);group=grp.getgrnam(GROUP)
        source=Path(__file__).with_name('model_transport.py')
        spec=importlib.util.spec_from_file_location('model_transport',source);policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
        for name in ('host','client'):run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',root/name])
        keys=root/'authorized_keys';keys.write_text(policy.authorized_key((root/'client.pub').read_text().strip()));keys.chmod(0o644)
        exports=root/'exports';exports.mkdir(mode=0o755)
        releases=exports/'releases';releases.mkdir(mode=0o755)
        release=releases/('a'*40);release.mkdir(mode=0o755)
        (release/'manifest.json').write_text('{"release_sha":"'+('a'*40)+'"}\n');(release/'manifest.json').chmod(0o444)
        (releases/'current').symlink_to('a'*40)
        inference=root/'inference';inference.mkdir(mode=0o710);os.chown(inference,1000,group.gr_gid)
        # Exercise the actual fixed-path bind implementation, with fixture-only
        # roots and group. The SSH-visible path remains the production path.
        policy.INFERENCE=inference;policy.EXPORT=exports;policy.GROUP=GROUP
        policy.bind_inference();mounted_target=exports/'run/radon-model-gateway'
        policy.bind_inference()  # An ordinary restart reuses the verified bind.
        run(['/usr/bin/umount',mounted_target]);mounted_target=None
        policy.bind_inference();mounted_target=exports/'run/radon-model-gateway'  # Reboot rebind.
        private=root/'control';private.mkdir(mode=0o700)
        def listener(path,*,group_gid=None):
            server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);server.bind(str(path));server.listen(8);server.settimeout(0.2)
            if group_gid is not None:os.chown(path,1000,group_gid);path.chmod(0o660)
            listeners.append(server)
            def serve():
                while True:
                    try:connection,_=server.accept()
                    except socket.timeout:continue
                    except OSError:return
                    with connection:
                        uid=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
                        connection.settimeout(2)
                        try:connection.recv(4096)
                        except OSError:continue
                        payload=json.dumps({'uid':uid}).encode()
                        connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(payload)).encode()+b'\r\nConnection: close\r\n\r\n'+payload)
            threading.Thread(target=serve,daemon=True).start()
        listener(inference/'gateway.sock',group_gid=group.gr_gid);listener(private/'control.sock')
        with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        configuration=root/'sshd_config'
        policy_text=policy.sshd_config().replace('/var/lib/radon-model-export',str(exports)).replace('/etc/radon/model-transport/authorized_keys',str(keys))
        configuration.write_text(f'Port {port}\nListenAddress 127.0.0.1\nHostKey {root}/host\nPidFile {root}/sshd.pid\nUsePAM no\nPasswordAuthentication no\nStrictModes yes\nLogLevel ERROR\nSubsystem sftp internal-sftp\n'+policy_text)
        Path('/run/sshd').mkdir(mode=0o755,exist_ok=True)
        run(['/usr/sbin/sshd','-t','-f',configuration])
        log=(root/'sshd.log').open('wb')
        daemon=subprocess.Popen(['/usr/sbin/sshd','-D','-e','-f',str(configuration)],stdout=log,stderr=log,env=ENV);processes.append(daemon)
        known=root/'known_hosts';known.write_text('[127.0.0.1]:'+str(port)+' '+(root/'host.pub').read_text())
        options=['-F','/dev/null','-o','BatchMode=yes','-o','IdentityAgent=none','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(known),'-i',str(root/'client')]
        destination=ACCOUNT+'@127.0.0.1'
        for _ in range(50):
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1',port))==0:break
            time.sleep(.1)
        local=root/'local.sock'
        tunnel=subprocess.Popen(['/usr/bin/ssh',*options,'-p',str(port),'-NT','-o','ExitOnForwardFailure=yes','-L',str(local)+':/run/radon-model-gateway/gateway.sock',destination],stdout=subprocess.DEVNULL,stderr=log,env=ENV);processes.append(tunnel)
        for _ in range(50):
            if local.exists():break
            if tunnel.poll() is not None:raise RuntimeError('model-only tunnel failed before binding')
            time.sleep(.1)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(5);client.connect(str(local));client.sendall(b'GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n');data=b''
            while True:
                chunk=client.recv(4096)
                if not chunk:break
                data+=chunk
        if json.loads(data.split(b'\r\n\r\n',1)[1])!={'uid':account.pw_uid}:raise RuntimeError('forwarding did not use the dedicated UID')
        batch=root/'sftp.batch'
        def sftp(text):
            batch.write_text(text)
            return run(['/usr/bin/sftp',*options,'-P',str(port),'-q','-b',batch,destination],check=False)
        received=root/'received.json'
        if sftp(f'get /releases/current/manifest.json {received}\n').returncode or not received.is_file():raise RuntimeError('read-only immutable export unavailable')
        if sftp(f'put {received} /releases/forbidden.json\n').returncode==0:raise RuntimeError('SFTP export allowed writes')
        if sftp(f'get /etc/passwd {root}/escaped\n').returncode==0:raise RuntimeError('SFTP escaped export chroot')
        tcp=run(['/usr/bin/ssh',*options,'-p',str(port),'-W','127.0.0.1:'+str(port),destination],check=False)
        if tcp.returncode==0:raise RuntimeError('TCP forwarding was permitted')
        # A second Unix forward cannot traverse the management parent.
        denied=root/'denied.sock'
        process=subprocess.Popen(['/usr/bin/ssh',*options,'-p',str(port),'-NT','-L',str(denied)+':'+str(private/'control.sock'),destination],stdout=subprocess.DEVNULL,stderr=log,env=ENV);processes.append(process)
        for _ in range(50):
            if denied.exists():break
            time.sleep(.1)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(3);client.connect(str(denied))
            try:
                client.sendall(b'GET /health HTTP/1.0\r\n\r\n')
                rejected=client.recv(4096)
            except (ConnectionResetError,OSError):rejected=b''
            if rejected:raise RuntimeError('private management socket was reachable')
        print('model transport verified: dedicated UID inference, read-only chroot, no TCP or management access')
        return 0
    finally:
        for process in reversed(processes):
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        for listener in listeners:listener.close()
        if mounted_target is not None:run(['/usr/bin/umount',mounted_target])
        if created_user:run(['/usr/sbin/userdel',ACCOUNT],check=False)
        if created_group:run(['/usr/sbin/groupdel',GROUP],check=False)
        shutil.rmtree(root)

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print('model transport integration failed: '+type(exc).__name__+': '+str(exc),file=sys.stderr)
        raise SystemExit(1)
