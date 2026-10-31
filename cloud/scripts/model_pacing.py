#!/usr/bin/env python3
"""Capture fixed managed NVIDIA account clocks before ephemeral jobs stop.

Root control plane only. Every source read executes as UID1000 using stdlib,
never host-root application imports. Staging contains only bounded numeric
pacing fields, never arbitrary source contents or provider credentials.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import secrets
import time

UNITS=('radon-api.service','radon-research.service','radon-research-archive.service','radon-marketdesk.service','radon-marketdesk-pdf.service','radon-monitor.service','radon-uw-research.service','radon-ai-cycle.service','radon-ai-cycle-backfill.service','radon-aa-frontier-refresh.service')
NODE_UNITS=('radon-newsfeed.service','radon-nextjs.service','radon-relay.service')
UNITS=(*UNITS,*NODE_UNITS,'radon-model-gateway.service','radon-subscription-vault.service','radon-uw-stream.service')
DESTINATION=Path('/var/lib/radon-private/model-gateway/pacing-imports')
TIMES=('next_at','cooldown_until','tripped_until','last_429_at')
# The code is fixed; the source path comes only from that old process's
# existing environment. Symlinks, nonregular/foreign files and oversized state
# are refused, and unknown JSON keys never leave the unprivileged reader.
READER=r'''
import json, math, os, stat, fcntl, time
from pathlib import Path
p=Path(os.environ.get('RADON_NVIDIA_RATE_STATE') or '/home/radon/.radon/nvidia-rate.json')
if not p.is_absolute():raise SystemExit(78)
def read(path,limit):
    try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return None
    try:
        s=os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or s.st_mode&0o022 or s.st_size>limit:raise ValueError()
        raw=os.read(fd,limit+1)
        if len(raw)>limit:raise ValueError()
        return raw
    finally:os.close(fd)
def number(value):
    if type(value) not in (float,int) or not math.isfinite(value) or value<0:raise ValueError()
    return value
lock=None
try:
    try:lock=os.open(str(p)+'.lock',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:pass
    if lock is not None:
        meta=os.fstat(lock)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.getuid() or meta.st_mode&0o022:raise ValueError()
        deadline=time.monotonic()+0.25
        while True:
            try:fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise TimeoutError()
                time.sleep(0.01)
    raw=read(p,65536);out={}
    if raw is not None:
        state=json.loads(raw)
        if not isinstance(state,dict):raise ValueError()
        for key in ('next_at','cooldown_until','tripped_until','last_429_at'):
            if key in state:out[key]=number(state[key])
        if 'consecutive_429' in state:
            value=state['consecutive_429']
            if type(value) is not int or value<0:raise ValueError()
            out['consecutive_429']=value
        if 'last_auth_failure' in state:
            row=state['last_auth_failure']
            if not isinstance(row,dict) or row.get('status') not in (401,403):raise ValueError()
            out['last_auth_failure']={'at':number(row.get('at')),'status':row['status']}
    trip=read(str(p)+'.tripped',128)
    if trip is not None:
        until=number(float(trip.decode().strip()))
        out['tripped_until']=max(out.get('tripped_until',0),until)
    print(json.dumps(out,sort_keys=True,allow_nan=False))
except Exception:
    raise SystemExit(78)
finally:
    if lock is not None:os.close(lock)
'''

NODE_READER=r'''
const fs=require('node:fs');const path=require('node:path');
function read(file,limit){
 let fd;try{fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW|fs.constants.O_NONBLOCK)}catch(e){if(e.code==='ENOENT')return null;throw e}
 try{let s=fs.fstatSync(fd);if(!s.isFile()||s.uid!==process.getuid()||(s.mode&0o022)||s.size>limit)throw Error();let b=Buffer.alloc(limit+1);let n=fs.readSync(fd,b,0,b.length,null);if(n>limit)throw Error();return b.subarray(0,n).toString('utf8')}finally{fs.closeSync(fd)}
}
function number(x){if(typeof x!=='number'||!Number.isFinite(x)||x<0)throw Error();return x}
try{
 const p=process.env.RADON_NVIDIA_RATE_STATE||'/home/radon/.radon/nvidia-rate.json';if(!path.isAbsolute(p))throw Error();let out={};let raw=read(p,65536);
 if(raw!==null){let s=JSON.parse(raw);if(!s||typeof s!=='object'||Array.isArray(s))throw Error();for(let k of ['next_at','cooldown_until','tripped_until','last_429_at'])if(k in s)out[k]=number(s[k]);
 if('consecutive_429' in s){if(!Number.isSafeInteger(s.consecutive_429)||s.consecutive_429<0)throw Error();out.consecutive_429=s.consecutive_429}
 if('last_auth_failure' in s){let r=s.last_auth_failure;if(!r||![401,403].includes(r.status))throw Error();out.last_auth_failure={at:number(r.at),status:r.status}}}
 let trip=read(p+'.tripped',128);if(trip!==null){if(!trip.trim())throw Error();out.tripped_until=Math.max(out.tripped_until||0,number(Number(trip.trim())))}
 process.stdout.write(JSON.stringify(out)+'\n');
}catch(e){process.exit(78)}
'''

def normalized(raw):
    if len(raw)>4096:raise ValueError('pacing export exceeds bounds')
    data=json.loads(raw)
    if not isinstance(data,dict) or set(data)-{*TIMES,'consecutive_429','last_auth_failure'}:raise ValueError('unexpected pacing fields')
    for key,value in data.items():
        if key=='last_auth_failure':
            if not isinstance(value,dict) or set(value)!={'at','status'} or type(value['status'])is not int or value['status']not in(401,403):raise ValueError('invalid auth pacing')
            value=value['at']
        if type(value)not in(float,int) or not math.isfinite(value) or value<0:raise ValueError('invalid pacing clock')
        if key=='consecutive_429' and type(value)is not int:raise ValueError('invalid pacing count')  # gitleaks:allow -- public numeric pacing field, not a credential
    return data

def merge(left,right):
    result=dict(left)
    for key,value in right.items():
        if key=='last_auth_failure':
            if value['at']>result.get(key,{}).get('at',-1):result[key]=value
        else:result[key]=max(result.get(key,0),value)
    return result

def store(directory,name,data,*,uid=1000,gid=1000):
    if name not in {'host.json',*(unit+'.json' for unit in UNITS)}:raise ValueError('invalid managed pacing identity')
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    temporary='.capture-'+secrets.token_hex(12)
    try:
        info=os.fstat(fd)
        if info.st_uid!=uid or info.st_mode&0o022:raise ValueError('unsafe pacing staging directory')
        try:previous=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
        except FileNotFoundError:previous=None
        if previous is not None:
            try:
                meta=os.fstat(previous)
                if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=uid or meta.st_mode&0o077:raise ValueError('unsafe captured pacing file')
                data=merge(normalized(os.read(previous,4097)),data)
            finally:os.close(previous)
        if not data:return
        target=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        try:
            os.fchown(target,uid,gid)
            os.write(target,(json.dumps(data,sort_keys=True,allow_nan=False)+'\n').encode());os.fsync(target)
        finally:os.close(target)
        os.replace(temporary,name,src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
    finally:
        try:os.unlink(temporary,dir_fd=fd)
        except FileNotFoundError:pass
        os.close(fd)

def invoke(command,*,env=None,timeout=3):
    result=subprocess.run(command,capture_output=True,timeout=timeout,check=False,env=env)
    if result.returncode:raise RuntimeError('managed pacing capture refused')
    return result.stdout

def capture(engine,directory=DESTINATION,*,only_unit=None):
    if only_unit is not None and only_unit not in UNITS:raise ValueError('invalid managed pacing unit')
    deadline=time.monotonic()+30
    running=invoke([engine,'ps','--format','{{.Names}}']).decode().splitlines()
    captured=0
    for unit in UNITS:
        if (only_unit is not None and unit!=only_unit) or unit not in running:continue
        if time.monotonic()>=deadline:raise TimeoutError('pacing capture deadline exceeded')
        command=['/usr/local/bin/node','--eval',NODE_READER] if unit in NODE_UNITS else ['/usr/local/bin/python','-I','-c',READER]
        raw=invoke([engine,'exec','--user','1000:1000',unit,*command],timeout=min(3,max(0.1,deadline-time.monotonic())))
        data=normalized(raw)
        if data:store(directory,unit+'.json',data);captured+=1
    if only_unit is not None:
        print(json.dumps({'captured_pacing_sources':captured},sort_keys=True));return
    host_env={'PATH':'/usr/bin:/bin','HOME':'/home/radon','LANG':'C.UTF-8'}
    # Only root's existing fixed environment file can select a legacy custom
    # host rate-state path. It is read later as UID1000, never as root.
    with Path('/etc/radon/env').open() as source:
        for line in source:
            key,sep,value=line.rstrip('\n').partition('=')
            if sep and key=='RADON_NVIDIA_RATE_STATE':host_env[key]=value
    data=normalized(invoke(['/usr/bin/setpriv','--reuid=1000','--regid=1000','--clear-groups','/usr/bin/python3','-I','-c',READER],env=host_env))
    if data:store(directory,'host.json',data);captured+=1
    print(json.dumps({'captured_pacing_sources':captured},sort_keys=True))

def main(arguments):
    if os.geteuid()!=0 or len(arguments)not in(1,2) or arguments[0]not in('/usr/bin/podman','/usr/bin/docker'):return 64
    if len(arguments)==2 and arguments[1]not in UNITS:return 64
    try:capture(arguments[0],only_unit=arguments[1] if len(arguments)==2 else None);return 0
    except Exception as exc:
        print('model pacing capture refused: '+type(exc).__name__,file=sys.stderr);return 78
if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
